"""Glossar-Seite im Dashboard (Issue #688)."""
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web import app as web_app
from sc_digger.web.glossary import GLOSSAR

ROOT = Path(__file__).resolve().parents[1]
AUTH = ("sc", "pw-glossar-test")


def _html(tmp_path, path="/glossar"):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
        "health_db_path": str(tmp_path / "seen.sqlite"),
    }
    cfg.raw["download"] = {"inbox_dir": str(tmp_path / "inbox"), "collection_dir": str(tmp_path / "collection")}
    client = TestClient(web_app.create_app(cfg, password=AUTH[1]))
    r = client.get(path, auth=AUTH)
    assert r.status_code == 200
    return r.text


def test_glossar_verlangt_anmeldung(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {"db_path": str(tmp_path / "s.sqlite"), "track_db_path": str(tmp_path / "t.sqlite")}
    client = TestClient(web_app.create_app(cfg, password=AUTH[1]))
    assert client.get("/glossar").status_code == 401


def test_begriff_ohne_alternativen_hat_keinen_nicht_hinweis(tmp_path):
    html = _html(tmp_path)
    ohne = [b for b, (_e, alt) in GLOSSAR.items() if not alt]
    assert ohne
    # Je Begriff den Eintrag (dd) isolieren: Erklärung bis zum nächsten </dd>.
    for begriff in ohne:
        erklaerung = GLOSSAR[begriff][0]
        ab = html.index(erklaerung)
        assert "nicht:" not in html[ab:html.index("</dd>", ab)], begriff


def test_alternativen_werden_aufgelistet(tmp_path):
    html = _html(tmp_path)
    assert "nicht: abgelehnt, abgewiesen, aussortiert" in html


def test_legende_nutzt_makro_klassen(tmp_path):
    html = _html(tmp_path)
    for klasse in ("badge-ok", "badge-info", "badge-err", "badge-warn", "badge-purple", "badge-muted"):
        assert klasse in html


def test_glossar_nicht_in_schnellsuche_und_navigation(tmp_path):
    from sc_digger.web.navigation import NAV_GROUPS
    assert "/glossar" not in repr(NAV_GROUPS)
