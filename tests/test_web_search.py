"""Tests für die Track-Schnellsuche (GET /api/search, Issue #678)."""
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.web import app as web_app

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-suche-4711"
AUTH = ("sc", PASSWORD)


def _setup(tmp_path):
    track_db = tmp_path / "track.sqlite"
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(track_db),
        "health_db_path": str(tmp_path / "seen.sqlite"),
    }
    cfg.raw["download"] = {
        "inbox_dir": str(tmp_path / "inbox"),
        "collection_dir": str(tmp_path / "collection"),
    }
    return track_db, TestClient(web_app.create_app(cfg, password=PASSWORD))


def test_suche_max_fuenf_treffer(tmp_path):
    """Liefert höchstens 5 Treffer zurück (Issue #678)."""
    track_db, client = _setup(tmp_path)
    with TrackDB(track_db) as db:
        for i in range(8):
            db.upsert_track(TrackRecord(
                path=f"/inbox/artist_{i} - track_{i}.mp3",
                mtime=1.0,
                size=1000,
                artist="Hard Techno Artist",
                title=f"Hammer Track {i}",
                status="inbox",
            ))

    antwort = client.get("/api/search?q=Hammer", auth=AUTH)
    assert antwort.status_code == 200
    data = antwort.json()
    assert "tracks" in data
    assert len(data["tracks"]) == 5


def test_suche_folgt_der_reihenfolge_der_explorersuche(tmp_path):
    """Die Reihenfolge der Treffer entspricht der Explorersuche (id ASC)."""
    track_db, client = _setup(tmp_path)
    with TrackDB(track_db) as db:
        for i in [3, 1, 2]:
            db.upsert_track(TrackRecord(
                path=f"/inbox/track_{i}.mp3",
                mtime=1.0,
                size=1000,
                artist=f"Artist {i}",
                title=f"Titel {i}",
                status="inbox",
            ))

    antwort = client.get("/api/search?q=Artist", auth=AUTH)
    assert antwort.status_code == 200
    treffer = antwort.json()["tracks"]
    assert len(treffer) == 3
    labels = [t["label"] for t in treffer]
    assert labels == ["Artist 3 – Titel 3", "Artist 1 – Titel 1", "Artist 2 – Titel 2"]


def test_suche_mit_sc_id_link(tmp_path):
    """Track mit SoundCloud-ID verlinkt auf /track/sc/<sc_id>."""
    track_db, client = _setup(tmp_path)
    with TrackDB(track_db) as db:
        db.upsert_track(TrackRecord(
            path="/inbox/[123456789] Artist - Title.mp3",
            mtime=1.0,
            size=1000,
            artist="Artist",
            title="Title",
            status="inbox",
        ))

    antwort = client.get("/api/search?q=Title", auth=AUTH)
    assert antwort.status_code == 200
    treffer = antwort.json()["tracks"]
    assert len(treffer) == 1
    assert treffer[0]["href"] == "/track/sc/123456789"
    assert treffer[0]["label"] == "Artist – Title"


def test_suche_ohne_sc_id_link(tmp_path):
    """Track ohne SoundCloud-ID verlinkt auf /tracks?q=<artist+title>."""
    track_db, client = _setup(tmp_path)
    with TrackDB(track_db) as db:
        db.upsert_track(TrackRecord(
            path="/inbox/NoSC - OnlyTitle.mp3",
            mtime=1.0,
            size=1000,
            artist="NoSC",
            title="OnlyTitle",
            status="inbox",
        ))

    antwort = client.get("/api/search?q=OnlyTitle", auth=AUTH)
    assert antwort.status_code == 200
    treffer = antwort.json()["tracks"]
    assert len(treffer) == 1
    assert treffer[0]["href"] == "/tracks?q=NoSC+OnlyTitle"
    assert treffer[0]["label"] == "NoSC – OnlyTitle"


def test_suche_weniger_als_zwei_zeichen(tmp_path):
    """Weniger als 2 Zeichen liefern leere Trefferliste ohne Fehler."""
    track_db, client = _setup(tmp_path)
    with TrackDB(track_db) as db:
        db.upsert_track(TrackRecord(
            path="/inbox/track.mp3",
            mtime=1.0,
            size=1000,
            artist="Artist",
            title="Title",
            status="inbox",
        ))

    for q in ["", "a", "   "]:
        antwort = client.get(f"/api/search?q={q}", auth=AUTH)
        assert antwort.status_code == 200
        assert antwort.json() == {"tracks": []}

    # Ohne q-Parameter
    antwort = client.get("/api/search", auth=AUTH)
    assert antwort.status_code == 200
    assert antwort.json() == {"tracks": []}


def test_suche_erfordert_basic_auth(tmp_path):
    """Unangemeldete Anfragen werden mit 401 abgewiesen."""
    _, client = _setup(tmp_path)
    antwort = client.get("/api/search?q=test")
    assert antwort.status_code == 401
