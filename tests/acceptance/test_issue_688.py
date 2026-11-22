"""Akzeptanztests: Glossar-Seite im Dashboard (Issue #688)."""
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web import app as web_app
from sc_digger.web.glossary import GLOSSAR, STATUS_BADGES

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-glossar-4711"
AUTH = ("sc", PASSWORD)


def _client(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
        "health_db_path": str(tmp_path / "seen.sqlite"),
    }
    cfg.raw["download"] = {
        "inbox_dir": str(tmp_path / "inbox"),
        "collection_dir": str(tmp_path / "collection"),
    }
    return TestClient(web_app.create_app(cfg, password=PASSWORD))


def test_seite_zeigt_jeden_begriff(tmp_path):
    html = _client(tmp_path).get("/glossar", auth=AUTH).text
    assert 'id="glossar"' in html
    for begriff in GLOSSAR:
        assert begriff in html, begriff


def test_erklaerung_und_alternativen_stehen_dabei(tmp_path):
    html = _client(tmp_path).get("/glossar", auth=AUTH).text
    erklaerung = GLOSSAR["Quarantäne"][0]
    assert erklaerung in html
    assert "nicht:" in html


def test_legende_zeigt_alle_status_plaketten(tmp_path):
    html = _client(tmp_path).get("/glossar", auth=AUTH).text
    for wort, _klasse in STATUS_BADGES.values():
        assert wort in html, wort
    assert "badge-err" in html


def test_kopfzeile_verlinkt_das_glossar(tmp_path):
    html = _client(tmp_path).get("/", auth=AUTH).text
    assert 'href="/glossar"' in html
