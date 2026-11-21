"""Akzeptanztests für das Wartungsgerüst im Dashboard (Issue #655)."""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.tasks import finish_task, read_task_state, start_task

PASSWORD = "wartung-test-password"
AUTH = ("sc-digger", PASSWORD)
NOW = datetime(2026, 11, 20, 12, 0, 0)


def _cfg(tmp_path: Path) -> Config:
    return Config(raw={
        "state": {
            "db_path": str(tmp_path / "seen.sqlite"),
            "track_db_path": str(tmp_path / "tracks.sqlite"),
            "health_db_path": str(tmp_path / "seen.sqlite"),
        },
        "download": {
            "inbox_dir": str(tmp_path / "inbox"),
            "collection_dir": str(tmp_path / "collection"),
        },
    })


def test_start_task_sperrt_denselben_auftrag(tmp_path: Path):
    state = tmp_path / "tasks.json"
    freigeben = threading.Event()

    gestartet = start_task(
        "scout", lambda: (freigeben.wait(5), "fertig")[1], state_path=state, now=NOW
    )

    assert gestartet is True
    assert read_task_state(state, "scout")["state"] == "running"
    assert read_task_state(state, "scout")["action"] == "scout"
    assert start_task("scout", lambda: "fertig", state_path=state, now=NOW) is False

    freigeben.set()


def test_andere_aktion_startet_trotz_laufender(tmp_path: Path):
    state = tmp_path / "tasks.json"
    freigeben = threading.Event()

    start_task("scout", lambda: (freigeben.wait(5), "fertig")[1], state_path=state, now=NOW)

    assert start_task("watch", lambda: "fertig", state_path=state, now=NOW) is True

    freigeben.set()


def test_finish_task_schreibt_ergebnis(tmp_path: Path):
    state = tmp_path / "tasks.json"

    finish_task(state, "watch", ok=False, message="Wächter abgebrochen", now=NOW)

    fertig = read_task_state(state, "watch")
    assert fertig["state"] == "error"
    assert fertig["message"] == "Wächter abgebrochen"


def test_read_task_state_ohne_datei_ist_none(tmp_path: Path):
    assert read_task_state(tmp_path / "fehlt.json", "scout") is None


def test_api_startet_nur_bekannte_auftraege(tmp_path: Path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg, password=PASSWORD, maintenance_runners={"scout": lambda: "fertig"})

    with TestClient(app) as client:
        assert client.post("/api/maintenance/gibtsnicht", auth=AUTH).status_code == 404

        erster = client.post("/api/maintenance/scout", auth=AUTH)
        assert erster.status_code == 202
        assert erster.json()["action"] == "scout"

        zweiter = client.post("/api/maintenance/scout", auth=AUTH)
        assert zweiter.status_code in (202, 409)

        uebersicht = client.get("/api/maintenance", auth=AUTH)
        assert uebersicht.status_code == 200
        assert uebersicht.json()["scout"]["action"] == "scout"
