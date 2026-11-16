"""Unit-Tests für die Kuratoren-Übersicht (Issue #567)."""
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.curator import CuratorEntry, curator_overview
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.web.app import create_app

PASSWORD = "test-curator-pass"
AUTH = ("sc", PASSWORD)


def _track(
    db: TrackDB,
    path: str,
    artist: str,
    *,
    rating: int | None = None,
    bpm: float | None = None,
    status: str = "archive",
    key: str | None = None,
    title: str | None = None,
):
    return db.upsert_track(
        TrackRecord(
            path=path,
            mtime=1.0,
            size=1000,
            artist=artist,
            title=title,
            rb_rating=rating,
            bpm=bpm,
            key_camelot=key,
            status=status,
        )
    )


def test_curator_limit_beschraenkt_rueckgabe(tmp_path: Path):
    """Testfall mit limit: Mehrere Artists, nur die obersten `limit` werden zurückgegeben."""
    db_file = tmp_path / "limit_test.sqlite"
    with TrackDB(db_file) as db:
        for i in range(5):
            # Artist0: 5 Tracks, Artist1: 4 Tracks, Artist2: 3 Tracks, ...
            for j in range(5 - i):
                _track(db, f"/x/a{i}_{j}.flac", f"Artist{i}", bpm=140.0 + j)

        # min_tracks=1, limit=2 -> genau die 2 Artists mit den meisten Tracks (Artist0 und Artist1)
        res = curator_overview(db, min_tracks=1, limit=2)
        assert len(res) == 2
        assert [e.artist for e in res] == ["Artist0", "Artist1"]
        assert res[0].track_count == 5
        assert res[1].track_count == 4

        # limit=0 -> leere Liste
        assert curator_overview(db, min_tracks=1, limit=0) == []


def test_curator_artist_ohne_tracks(tmp_path: Path):
    """Testfall mit Artist ohne Tracks (bzw. ohne Sammlungs-Tracks)."""
    db_file = tmp_path / "no_tracks.sqlite"
    with TrackDB(db_file) as db:
        # ArtistInbox hat nur Tracks in 'inbox' (keine Sammlungs-Tracks)
        _track(db, "/x/inbox1.flac", "ArtistInbox", status="inbox")
        _track(db, "/x/inbox2.flac", "ArtistInbox", status="inbox")

        # ArtistArchive hat 2 Sammlungs-Tracks
        _track(db, "/x/arc1.flac", "ArtistArchive", status="archive")
        _track(db, "/x/arc2.flac", "ArtistArchive", status="archive")

        # min_tracks=1 -> ArtistInbox hat 0 Sammlungs-Tracks und taucht nicht auf
        res = curator_overview(db, min_tracks=1)
        artists = [e.artist for e in res]
        assert "ArtistInbox" not in artists
        assert artists == ["ArtistArchive"]

        # Leere Datenbank / keine Tracks für einen gesuchten Artist
        db_empty = tmp_path / "empty.sqlite"
        with TrackDB(db_empty) as empty_db:
            assert curator_overview(empty_db, min_tracks=1) == []


def test_curator_track_bpm_sortierung_und_details(tmp_path: Path):
    """Prüft aufsteigende Sortierung nach BPM und Behandlung von None."""
    db_file = tmp_path / "bpm_sort.sqlite"
    with TrackDB(db_file) as db:
        _track(db, "/x/t1.flac", "DJ Test", bpm=165.0, rating=4)
        _track(db, "/x/t2.flac", "DJ Test", bpm=None, rating=5)
        _track(db, "/x/t3.flac", "DJ Test", bpm=145.0, rating=2)
        _track(db, "/x/t4.flac", "DJ Test", bpm=150.0, rating=4)

        res = curator_overview(db, min_tracks=3)
        assert len(res) == 1
        entry = res[0]
        assert entry.artist == "DJ Test"
        assert entry.track_count == 4
        # 3 von 4 haben rating >= 4 -> 0.75
        assert abs(entry.rated_share - 0.75) < 1e-9
        # BPMs: 165, 145, 150 -> Schnitt 153.3333...
        assert abs(entry.avg_bpm - (165.0 + 145.0 + 150.0) / 3) < 1e-9
        # Tracks sortiert: 145, 150, 165, None
        bpms = [t.bpm for t in entry.tracks]
        assert bpms == [145.0, 150.0, 165.0, None]


def test_web_kuratoren_endpoints(tmp_path: Path):
    """Testet GET /kuratoren und GET /api/kuratoren."""
    root = Path(__file__).resolve().parents[1]
    cfg = Config.load(root / "config.yaml")
    track_db_path = tmp_path / "tracks.sqlite"
    state_db_path = tmp_path / "state.sqlite"
    cfg.raw["state"] = {
        "db_path": str(state_db_path),
        "track_db_path": str(track_db_path),
    }

    with TrackDB(track_db_path) as db:
        for i in range(3):
            _track(
                db,
                f"/x/c_{i}.flac",
                "KuratorAlpha",
                rating=4 + (i % 2),
                bpm=150.0 + i,
                key="8A",
                title=f"Track {i}",
            )

    client = TestClient(create_app(cfg, password=PASSWORD))

    # 1. API Route: GET /api/kuratoren
    api_resp = client.get("/api/kuratoren", auth=AUTH)
    assert api_resp.status_code == 200
    data = api_resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    item = data[0]
    assert item["artist"] == "KuratorAlpha"
    assert item["track_count"] == 3
    assert item["rated_share"] == 1.0
    assert abs(item["avg_bpm"] - 151.0) < 1e-9
    assert "tracks" not in item  # keine Track-Objekte im JSON

    # 2. Web Route: GET /kuratoren
    web_resp = client.get("/kuratoren", auth=AUTH)
    assert web_resp.status_code == 200
    assert "Kuratoren" in web_resp.text
    assert "KuratorAlpha" in web_resp.text
    assert "151.0 BPM" in web_resp.text
    assert "8A" in web_resp.text
    assert "Track 0" in web_resp.text
