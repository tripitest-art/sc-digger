"""Akzeptanztests: Gate öffnen und Datei ablegen auf der Track-Detailseite (Issue #708)."""
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.web import app as web_app

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-track-actions-4711"
AUTH = ("sc", PASSWORD)
SC = 4711
GATE_URL = "https://gate.example/track"


def _client(tmp_path, gate_url=GATE_URL):
    track_db = tmp_path / "track.sqlite"
    seen_db = tmp_path / "seen.sqlite"
    with TrackDB(track_db) as db:
        db.upsert_track(TrackRecord(
            path="/inbox/alpha.mp3", mtime=1.0, size=1000, artist="Alpha",
            title="Erster Track", bpm=155.0, key_camelot="8A", status="inbox", sc_id=SC,
        ))
        if gate_url:
            db.upsert_gate_item(SC, "Erster Track", "Alpha", gate_url)
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(seen_db),
        "track_db_path": str(track_db),
        "health_db_path": str(seen_db),
    }
    return TestClient(web_app.create_app(cfg, password=PASSWORD))


def test_gate_laesst_sich_oeffnen(tmp_path):
    html = _client(tmp_path).get(f"/track/sc/{SC}", auth=AUTH).text
    assert "Gate öffnen" in html
    assert GATE_URL in html


def test_datei_kann_direkt_abgelegt_werden(tmp_path):
    html = _client(tmp_path).get(f"/track/sc/{SC}", auth=AUTH).text
    assert 'class="track-upload"' in html
    assert f'data-sc-id="{SC}"' in html
    assert "/api/upload-intake" in html


def test_ohne_gate_kein_gate_link(tmp_path):
    html = _client(tmp_path, gate_url=None).get(f"/track/sc/{SC}", auth=AUTH).text
    assert "Gate öffnen" not in html
    # Das Dateifeld bleibt: man kann auch ohne Gate eine Datei ablegen.
    assert 'class="track-upload"' in html
