"""Eigene Tests für das Wartungsgerüst (Issue #655)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.tasks import (
    action_labels,
    default_runners,
    finish_task,
    read_task_state,
    start_task,
)

PASSWORD = "wartung-eigene-tests"
AUTH = ("sc-digger", PASSWORD)


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


def _warte_auf(state: Path, action: str, ziel: str) -> dict:
    for _ in range(100):
        eintrag = read_task_state(state, action)
        if eintrag and eintrag["state"] == ziel:
            return eintrag
        time.sleep(0.05)
    raise AssertionError(f"{action} erreicht {ziel} nicht")


def test_runner_ergebnis_wird_gespeichert(tmp_path: Path):
    state = tmp_path / "t.json"
    assert start_task("taste", lambda: "alles gut", state_path=state) is True
    fertig = _warte_auf(state, "taste", "done")
    assert fertig["message"] == "alles gut"
    assert fertig["finished_at"]


def test_ausnahme_verraet_keinen_text(tmp_path: Path):
    state = tmp_path / "t.json"

    def kaputt() -> str:
        raise ValueError("geheimer Pfad /etc/passwd")

    start_task("watch", kaputt, state_path=state)
    fehler = _warte_auf(state, "watch", "error")
    assert fehler["message"] == "ValueError: Lauf abgebrochen"
    assert "passwd" not in json.dumps(fehler)


def test_andere_eintraege_bleiben_erhalten(tmp_path: Path):
    state = tmp_path / "t.json"
    finish_task(state, "watch", ok=True, message="alt")
    freigeben = threading.Event()
    start_task("scout", lambda: (freigeben.wait(5), "x")[1], state_path=state)
    assert read_task_state(state, "watch")["message"] == "alt"
    freigeben.set()
    _warte_auf(state, "scout", "done")
    assert read_task_state(state, "watch")["state"] == "done"


def test_nachricht_wird_gekuerzt_und_kaputte_datei_ist_none(tmp_path: Path):
    state = tmp_path / "t.json"
    finish_task(state, "scout", ok=True, message="x" * 500)
    assert len(read_task_state(state, "scout")["message"]) == 300
    state.write_text("[1, 2]", encoding="utf-8")
    assert read_task_state(state, "scout") is None
    state.write_text("kein json", encoding="utf-8")
    assert read_task_state(state, "scout") is None


def test_running_aus_datei_sperrt_nach_neustart(tmp_path: Path):
    state = tmp_path / "t.json"
    state.write_text(json.dumps({"scout": {"action": "scout", "state": "running"}}), encoding="utf-8")
    assert start_task("scout", lambda: "x", state_path=state) is False


def test_standard_aktionen_und_labels(tmp_path: Path):
    runners = default_runners(_cfg(tmp_path))
    assert set(runners) == {"scout", "watch", "taste"} == set(action_labels())


def test_api_409_bei_laufender_aktion_und_fremder_origin(tmp_path: Path):
    freigeben = threading.Event()
    app = create_app(_cfg(tmp_path), password=PASSWORD,
                     maintenance_runners={"scout": lambda: (freigeben.wait(5), "ok")[1]})
    kopf = {"Content-Type": "application/json"}
    with TestClient(app) as client:
        assert client.post("/api/maintenance/scout", auth=AUTH, headers=kopf).status_code == 202
        assert client.post("/api/maintenance/scout", auth=AUTH, headers=kopf).status_code == 409
        fremd = client.post("/api/maintenance/scout", auth=AUTH,
                            headers={**kopf, "Origin": "http://boese.example"})
        assert fremd.status_code == 403
        assert client.get("/api/maintenance", auth=AUTH).json()["scout"]["state"] == "running"
        assert "Wartung" in client.get("/", auth=AUTH).text
        freigeben.set()
