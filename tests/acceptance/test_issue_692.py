"""Akzeptanztests: Handkorrektur im Track-Lebenslauf (Issue #692)."""
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.web import app as web_app

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-override-4711"
AUTH = ("sc", PASSWORD)
SC = 4711


def _client(tmp_path, override=None):
    track_db = tmp_path / "track.sqlite"
    seen_db = tmp_path / "seen.sqlite"
    with TrackDB(track_db) as db:
        db.upsert_track(TrackRecord(
            path="/inbox/alpha.mp3", mtime=1.0, size=1000, artist="Alpha",
            title="Erster Track", bpm=155.0, key_camelot="8A", status="inbox", sc_id=SC,
        ))
        if override:
            db.set_match_override(SC, override, "Dashboard")
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(seen_db),
        "track_db_path": str(track_db),
        "health_db_path": str(seen_db),
    }
    return TestClient(web_app.create_app(cfg, password=PASSWORD))


def test_lebenslauf_zeigt_die_handkorrektur(tmp_path):
    client = _client(tmp_path, override="already_in_collection")
    html = client.get(f"/track/sc/{SC}", auth=AUTH).text
    assert "Handkorrektur" in html
    assert "bereits in Sammlung" in html


def test_ohne_korrektur_kein_schritt(tmp_path):
    client = _client(tmp_path)
    html = client.get(f"/track/sc/{SC}", auth=AUTH).text
    assert "Handkorrektur" not in html


def test_zweite_korrekturform_wird_uebersetzt(tmp_path):
    client = _client(tmp_path, override="not_duplicate")
    html = client.get(f"/track/sc/{SC}", auth=AUTH).text
    assert "kein Duplikat" in html
