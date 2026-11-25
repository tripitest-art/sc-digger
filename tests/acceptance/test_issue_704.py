"""Akzeptanztests: Suche im Konfigeditor (Issue #704)."""
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web import app as web_app

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-suche-editor-4711"
AUTH = ("sc", PASSWORD)


def _client(tmp_path):
    base = tmp_path / "config.yaml"
    base.write_text((ROOT / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    local = tmp_path / "config.local.yaml"
    local.write_text("", encoding="utf-8")
    cfg = Config.load(base, local=local)
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
        "health_db_path": str(tmp_path / "seen.sqlite"),
    }
    # Die Editor-Routen existieren nur mit base_path UND local_path.
    return TestClient(web_app.create_app(
        cfg, password=PASSWORD, base_path=base, local_path=local,
    ))


def test_editor_hat_suchfeld(tmp_path):
    html = _client(tmp_path).get("/config", auth=AUTH).text
    assert 'id="config-suche"' in html
    assert "Feld suchen" in html


def test_felder_tragen_suchtext(tmp_path):
    html = _client(tmp_path).get("/config", auth=AUTH).text
    assert "data-suchtext=" in html
    # ein bekannter Schlüssel muss im Suchtext auftauchen
    assert "bpm_min" in html


def test_leerzustand_ist_vorhanden(tmp_path):
    html = _client(tmp_path).get("/config", auth=AUTH).text
    assert 'id="config-keine-treffer"' in html
