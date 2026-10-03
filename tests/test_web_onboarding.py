"""Tests zum Erststart der Web-Oberfläche: Editor-Default und Link auf /config."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-4711"


def make_cfg(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def status_body(tmp_path, **kwargs) -> str:
    app = create_app(make_cfg(tmp_path), password=PASSWORD, **kwargs)
    r = TestClient(app).get("/", auth=("sc", PASSWORD))
    assert r.status_code == 200
    return r.text


def test_editor_available_only_with_both_paths(tmp_path):
    """editor_available ist genau dann True, wenn Basis- UND Lokalpfad gesetzt sind."""
    both = status_body(tmp_path, base_path=ROOT / "config.yaml",
                       local_path=tmp_path / "config.local.yaml")
    assert "/config" in both
    only_base = status_body(tmp_path, base_path=ROOT / "config.yaml")
    assert "/config" not in only_base
    only_local = status_body(tmp_path, local_path=tmp_path / "config.local.yaml")
    assert "/config" not in only_local


def test_editor_routes_unreachable_without_password(tmp_path):
    """Ohne Passwort startet die App nicht (ValueError), also gibt es keine Editor-Routen."""
    with pytest.raises(ValueError):
        create_app(make_cfg(tmp_path), password=None,
                   base_path=ROOT / "config.yaml",
                   local_path=tmp_path / "config.local.yaml")


def test_editor_point_without_password_when_anonymous(tmp_path):
    """Anonym bleibt /config gesperrt, wenn keine Pfade gesetzt sind (kein Editor)."""
    app = create_app(make_cfg(tmp_path), password=None, allow_anonymous=True)
    r = TestClient(app).get("/config")
    assert r.status_code == 404


def test_config_link_is_anchored_to_config_route(tmp_path):
    body = status_body(tmp_path, base_path=ROOT / "config.yaml",
                       local_path=tmp_path / "config.local.yaml")
    assert '<a href="/config">' in body
