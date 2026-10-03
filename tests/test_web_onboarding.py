"""Eigene Tests zum Erststart/Onboarding der Web-Oberfläche (Issue #172)."""
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


def test_editor_available_requires_both_paths(tmp_path):
    """Nur mit base_path UND local_path gibt es den Link auf /config."""
    base = ROOT / "config.yaml"
    local = tmp_path / "config.local.yaml"

    with_both = create_app(make_cfg(tmp_path), password=PASSWORD, base_path=base, local_path=local)
    assert '/config' in TestClient(with_both).get("/", auth=("sc", PASSWORD)).text

    only_base = create_app(make_cfg(tmp_path), password=PASSWORD, base_path=base)
    assert '/config' not in TestClient(only_base).get("/", auth=("sc", PASSWORD)).text

    only_local = create_app(make_cfg(tmp_path), password=PASSWORD, local_path=local)
    assert '/config' not in TestClient(only_local).get("/", auth=("sc", PASSWORD)).text


def test_config_routes_require_auth(tmp_path):
    app = create_app(make_cfg(tmp_path), password=PASSWORD,
                     base_path=ROOT / "config.yaml",
                     local_path=tmp_path / "config.local.yaml")
    client = TestClient(app)
    r = client.get("/config")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].lower().startswith("basic")
    assert client.get("/config", auth=("sc", "falsch")).status_code == 401


def test_config_route_absent_without_editor(tmp_path):
    app = create_app(make_cfg(tmp_path), password=PASSWORD)
    assert TestClient(app).get("/config", auth=("sc", PASSWORD)).status_code == 404


def test_status_page_without_editor_still_renders(tmp_path):
    app = create_app(make_cfg(tmp_path), password=PASSWORD)
    r = TestClient(app).get("/", auth=("sc", PASSWORD))
    assert r.status_code == 200
    assert "sc-digger Status" in r.text


def test_main_without_password_does_not_start(monkeypatch, capsys):
    import sc_digger.web.__main__ as entry
    monkeypatch.delenv("SC_DIGGER_WEB_PASSWORD", raising=False)
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)

    def no_start(*a, **k):
        raise AssertionError("uvicorn darf nicht starten")

    monkeypatch.setattr("uvicorn.run", no_start)
    assert entry.main(["--config", str(ROOT / "config.yaml")]) == 2
    assert "SC_DIGGER_WEB_PASSWORD" in capsys.readouterr().err


def test_entrypoint_exports_default_local_path():
    text = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    assert 'export SC_DIGGER_CONFIG_LOCAL="${SC_DIGGER_CONFIG_LOCAL:-/data/config.local.yaml}"' in text
