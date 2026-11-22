"""Akzeptanztests: Schnellsuche mit direkten Track-Treffern (Issue #678)."""
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.web import app as web_app

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-suche-4711"
AUTH = ("sc", PASSWORD)


def _setup(tmp_path):
    track_db = tmp_path / "track.sqlite"
    with TrackDB(track_db) as db:
        db.upsert_track(TrackRecord(
            path="/inbox/alpha.mp3",
            mtime=1.0,
            size=1000,
            artist="Alpha",
            title="Erster Track",
            bpm=155.0,
            key_camelot="8A",
            status="inbox",
        ))
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
    return TestClient(web_app.create_app(cfg, password=PASSWORD))


def test_suche_findet_den_track(tmp_path):
    client = _setup(tmp_path)
    antwort = client.get("/api/search?q=erster", auth=AUTH)
    assert antwort.status_code == 200
    treffer = antwort.json()["tracks"]
    assert len(treffer) == 1
    assert "Erster Track" in treffer[0]["label"]
    assert treffer[0]["href"].startswith("/track/") or treffer[0]["href"].startswith("/tracks?q=")


def test_ein_zeichen_liefert_keine_treffer(tmp_path):
    client = _setup(tmp_path)
    antwort = client.get("/api/search?q=e", auth=AUTH)
    assert antwort.status_code == 200
    assert antwort.json()["tracks"] == []


def test_ohne_treffer_leere_liste(tmp_path):
    client = _setup(tmp_path)
    antwort = client.get("/api/search?q=zzzzzz", auth=AUTH)
    assert antwort.status_code == 200
    assert antwort.json()["tracks"] == []


def test_seite_fragt_die_suche_ab(tmp_path):
    client = _setup(tmp_path)
    html = client.get("/tracks", auth=AUTH).text
    assert "/api/search" in html
    assert 'id="quick-search"' in html
