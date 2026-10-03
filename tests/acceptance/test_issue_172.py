"""Akzeptanztests: Onboarding der Web-Oberfläche (Editor standardmäßig an, Link auf /config)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-4711"


def make_cfg(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


@pytest.fixture(autouse=True)
def no_local_env(monkeypatch):
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)


# ------------------------------------------------------------------ entrypoint.sh
def test_entrypoint_enables_editor_by_default():
    """Im Container ist der Editor aktiv, sobald das Web-Passwort gesetzt ist."""
    text = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    block_start = text.index("SC_DIGGER_WEB_PASSWORD")
    block_end = text.index("exec python -m sc_digger.bot")
    block = text[block_start:block_end]
    assert "SC_DIGGER_CONFIG_LOCAL" in block, "entrypoint.sh setzt SC_DIGGER_CONFIG_LOCAL nicht"
    assert "/data/config.local.yaml" in block, "Default-Pfad der Override-Datei fehlt"
    # Default über ${VAR:-...}: eine vom Nutzer gesetzte Variable wird nicht überschrieben.
    assert "${SC_DIGGER_CONFIG_LOCAL:-" in block
    # Der Bot bleibt Hauptprozess.
    last_line = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")][-1]
    assert last_line.strip() == "exec python -m sc_digger.bot"


def test_entrypoint_starts_web_only_with_password():
    text = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    assert text.index("SC_DIGGER_WEB_PASSWORD") < text.index("sc_digger.web")
    if_pos = [ln for ln in text.splitlines() if "sc_digger.web" in ln and "--host" in ln]
    assert len(if_pos) == 1
    assert if_pos[0].strip().startswith("python -m sc_digger.web")


# ------------------------------------------------------------------ Statusseite verlinkt Editor
def test_status_page_links_to_config_when_editor_active(tmp_path):
    app = create_app(make_cfg(tmp_path), password=PASSWORD,
                     base_path=ROOT / "config.yaml",
                     local_path=tmp_path / "config.local.yaml")
    r = TestClient(app).get("/", auth=("sc", PASSWORD))
    assert r.status_code == 200
    assert '/config' in r.text, "Statusseite verlinkt den Konfigeditor nicht"


def test_status_page_has_no_config_link_without_editor(tmp_path):
    app = create_app(make_cfg(tmp_path), password=PASSWORD)
    r = TestClient(app).get("/", auth=("sc", PASSWORD))
    assert r.status_code == 200
    assert '/config' not in r.text


def test_config_page_reachable_via_default_container_setup(tmp_path, monkeypatch):
    """So startet der Container künftig: Passwort gesetzt, entrypoint.sh hat den Local-Pfad exportiert."""
    monkeypatch.setenv("SC_DIGGER_WEB_PASSWORD", PASSWORD)
    monkeypatch.setenv("SC_DIGGER_CONFIG_LOCAL", "/data/config.local.yaml")
    import sc_digger.web.__main__ as entry
    started = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: started.update(app=app, **kw))
    assert entry.main(["--config", str(ROOT / "config.yaml")]) == 0
    # Die App besitzt die Editor-Routen: /config antwortet (kein 404).
    client = TestClient(started["app"])
    assert client.get("/config", auth=("sc", PASSWORD)).status_code == 200


# ------------------------------------------------------------------ Doku
def test_readme_has_quickstart():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Erststart" in readme
    assert "SC_DIGGER_WEB_PASSWORD" in readme
    assert "8080" in readme


def test_env_example_explains_editor_default():
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    idx = env.index("SC_DIGGER_CONFIG_LOCAL=")
    comment = env[max(0, idx - 600):idx]
    assert "automatisch" in comment, "Kommentar erklärt den Container-Default nicht"
