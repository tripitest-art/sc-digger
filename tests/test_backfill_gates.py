from pathlib import Path
import sqlite3
import pytest

from sc_digger.db import TrackDB
from sc_digger.models import DownloadKind, Track
from scripts.backfill_gates import BackfillStats, resolve_paths, run_backfill


class FakeSoundCloudClient:
    def __init__(self, tracks_by_id: dict[int, Track]):
        self.tracks_by_id = tracks_by_id
        self.requested_chunks = []

    def _get(self, path: str, params: dict | None = None):
        if path == "/tracks":
            ids_str = (params or {}).get("ids", "")
            ids = [int(i.strip()) for i in ids_str.split(",") if i.strip()]
            self.requested_chunks.append(ids)
            return [
                {
                    "kind": "track",
                    "id": t.id,
                    "title": t.title,
                    "user": {"username": t.artist, "permalink_url": t.artist_url},
                    "permalink_url": t.url,
                    "created_at": t.created_at,
                    "duration": t.duration_ms,
                    "genre": t.genre,
                    "description": t.description,
                    "purchase_url": t.purchase_url,
                    "downloadable": t.downloadable,
                    "has_downloads_left": t.has_downloads_left,
                }
                for t in [self.tracks_by_id.get(i) for i in ids]
                if t is not None
            ]
        raise NotImplementedError(f"Path nicht unterstützt: {path}")

    def _to_track(self, d: dict) -> Track | None:
        sc_id = d.get("id")
        return self.tracks_by_id.get(sc_id)


def test_run_backfill_erkennt_gates_und_stores(tmp_path: Path):
    seen_db = tmp_path / "seen.sqlite"
    track_db = tmp_path / "tracks.sqlite"

    # seen.sqlite befüllen
    with sqlite3.connect(seen_db) as sdb:
        sdb.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        sdb.execute("INSERT INTO seen VALUES (101, 'https://soundcloud.com/dj/gate-1', 'DJ - Hyped Track', '2026-10-10 10:00:00')")
        sdb.execute("INSERT INTO seen VALUES (102, 'https://soundcloud.com/dj/stream-2', 'DJ - Stream Track', '2026-10-10 11:00:00')")
        sdb.execute("INSERT INTO seen VALUES (103, 'https://soundcloud.com/dj/store-3', 'DJ - Store Track', '2026-10-10 12:00:00')")
        sdb.execute("INSERT INTO seen VALUES (104, 'https://soundcloud.com/dj/already-4', 'DJ - Bereits erfasst', '2026-10-10 13:00:00')")

    # Vorhandene TrackDB mit bereits erfasstem Gate
    with TrackDB(track_db) as db:
        db.upsert_gate_item(104, "Bereits erfasst", "DJ", "https://hypeddit.com/old", "hypeddit")

    fake_tracks = {
        101: Track(
            id=101,
            title="Hyped Track",
            url="https://soundcloud.com/dj/gate-1",
            artist="DJ",
            artist_url="https://soundcloud.com/dj",
            created_at="2026-10-10T00:00:00Z",
            duration_ms=180000,
            genre="Schranz",
            tags=[],
            description="Free download at https://hypeddit.com/track/101",
            bpm=150.0,
            plays=10,
            likes=5,
            reposts=1,
            comments=0,
            downloadable=False,
            has_downloads_left=False,
            purchase_url=None,
            purchase_title=None,
        ),
        102: Track(
            id=102,
            title="Stream Track",
            url="https://soundcloud.com/dj/stream-2",
            artist="DJ",
            artist_url="https://soundcloud.com/dj",
            created_at="2026-10-10T00:00:00Z",
            duration_ms=180000,
            genre="Schranz",
            tags=[],
            description="Just a stream, no gate",
            bpm=150.0,
            plays=10,
            likes=5,
            reposts=1,
            comments=0,
            downloadable=False,
            has_downloads_left=False,
            purchase_url=None,
            purchase_title=None,
        ),
        103: Track(
            id=103,
            title="Store Track",
            url="https://soundcloud.com/dj/store-3",
            artist="DJ",
            artist_url="https://soundcloud.com/dj",
            created_at="2026-10-10T00:00:00Z",
            duration_ms=180000,
            genre="Schranz",
            tags=[],
            description="Buy here: https://artist.bandcamp.com/track/store-3",
            bpm=150.0,
            plays=10,
            likes=5,
            reposts=1,
            comments=0,
            downloadable=False,
            has_downloads_left=False,
            purchase_url="https://artist.bandcamp.com/track/store-3",
            purchase_title="Buy Track",
        ),
    }

    sc = FakeSoundCloudClient(fake_tracks)
    stats = run_backfill(seen_db, track_db, sc_client=sc, batch_size=2)

    assert stats.total_seen == 4
    assert stats.already_persisted == 1  # Track 104 war bereits in gate_items
    assert stats.checked == 3
    assert stats.gates_found == 1        # Track 101
    assert stats.stores_found == 1       # Track 103
    assert stats.stream_only == 1        # Track 102
    assert stats.errors == 0

    # Prüfe TrackDB Inhalte
    with TrackDB(track_db) as db:
        gates = {g["sc_id"]: g for g in db.get_gate_items()}
        assert 101 in gates
        assert gates[101]["gate_url"] == "https://hypeddit.com/track/101"
        assert gates[101]["gate_type"] == "hypeddit"

        stores = {s["sc_id"]: s for s in db.get_store_items()}
        assert 103 in stores
        assert stores[103]["purchase_url"] == "https://artist.bandcamp.com/track/store-3"


def test_run_backfill_dry_run_schreibt_nicht(tmp_path: Path):
    seen_db = tmp_path / "seen.sqlite"
    track_db = tmp_path / "tracks.sqlite"

    with sqlite3.connect(seen_db) as sdb:
        sdb.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        sdb.execute("INSERT INTO seen VALUES (201, 'https://soundcloud.com/dj/gate', 'DJ - Gate', '2026-10-10 10:00:00')")

    with TrackDB(track_db) as db:
        pass  # Erzeugt Tabellen

    fake_tracks = {
        201: Track(
            id=201,
            title="Gate Track",
            url="https://soundcloud.com/dj/gate",
            artist="DJ",
            artist_url="https://soundcloud.com/dj",
            created_at="2026-10-10T00:00:00Z",
            duration_ms=180000,
            genre="Schranz",
            tags=[],
            description="Download: https://hypeddit.com/track/201",
            bpm=150.0,
            plays=10,
            likes=5,
            reposts=1,
            comments=0,
            downloadable=False,
            has_downloads_left=False,
            purchase_url=None,
            purchase_title=None,
        )
    }

    sc = FakeSoundCloudClient(fake_tracks)
    stats = run_backfill(seen_db, track_db, sc_client=sc, dry_run=True)

    assert stats.gates_found == 1
    with TrackDB(track_db) as db:
        assert len(db.get_gate_items()) == 0


def test_run_backfill_datei_fehlt():
    with pytest.raises(FileNotFoundError):
        run_backfill("nicht_vorhanden.sqlite", "tracks.sqlite")


def test_resolve_paths():
    seen, track = resolve_paths(Path("nicht_existent.yaml"), "/custom/seen.sqlite", "/custom/tracks.sqlite")
    assert str(seen).replace("\\", "/") == "/custom/seen.sqlite"
    assert str(track).replace("\\", "/") == "/custom/tracks.sqlite"
