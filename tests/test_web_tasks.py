"""Eigene Tests für das Wartungsgerüst (Issue #655)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

import pytest

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
    erwartet = {
        "scout",
        "watch",
        "taste",
        "backfill-energy",
        "backfill-artist",
        "backfill-names",
        "backfill-bpm",
        "backfill-embeddings",
        "requeue",
        "backup",
        "audit",
        "curator-mining",
    }
    assert set(runners) == erwartet == set(action_labels())


def test_runner_backfill_energy(tmp_path: Path, monkeypatch):
    cfg = _cfg(tmp_path)
    aufgerufen = {}

    def fake_backfill(db, *, dry_run=False):
        aufgerufen["dry_run"] = dry_run
        aufgerufen["db_path"] = str(db.path)
        return {"set": 12, "skipped": 3, "failed": 0}

    monkeypatch.setattr("sc_digger.backfill.backfill_energy", fake_backfill)
    runners = default_runners(cfg)
    msg = runners["backfill-energy"]()
    assert aufgerufen["dry_run"] is False
    assert aufgerufen["db_path"] == cfg["state"]["track_db_path"]
    assert msg == "Energie: 12 gesetzt, 3 übersprungen"


def test_runner_backfill_bpm(tmp_path: Path, monkeypatch):
    cfg = _cfg(tmp_path)
    aufgerufen = {}

    def fake_backfill(db, *, dry_run=False, limit=None, snap_to=None):
        aufgerufen["dry_run"] = dry_run
        aufgerufen["snap_to"] = snap_to
        return {"set": 5, "unchanged": 2, "failed": 1}

    monkeypatch.setattr("sc_digger.backfill.backfill_bpm", fake_backfill)
    msg = default_runners(cfg)["backfill-bpm"]()
    assert aufgerufen["dry_run"] is False
    assert aufgerufen["snap_to"] == (140.0, 170.0)
    assert msg == "BPM: 5 korrigiert, 2 bestätigt, 1 fehlgeschlagen"


def test_runner_backfill_artist(tmp_path: Path, monkeypatch):
    from sc_digger.backfill_artist import BackfillResult

    cfg = _cfg(tmp_path)
    aufgerufen = {}

    def fake_artists(state_p, track_p, *, apply=False, backup_path=None):
        aufgerufen["state_p"] = str(state_p)
        aufgerufen["track_p"] = str(track_p)
        aufgerufen["apply"] = apply
        aufgerufen["backup_path"] = backup_path
        return BackfillResult(seen_updated=5, rejected_updated=2, unmatched=1)

    monkeypatch.setattr("sc_digger.backfill_artist.backfill_artists", fake_artists)
    runners = default_runners(cfg)
    msg = runners["backfill-artist"]()
    assert aufgerufen["apply"] is True
    assert aufgerufen["state_p"] == cfg["state"]["db_path"]
    assert aufgerufen["track_p"] == cfg["state"]["track_db_path"]
    assert aufgerufen["backup_path"] is not None
    assert msg == "Künstler: 5 seen, 2 rejected aktualisiert, 1 ohne Treffer"


def test_runner_backfill_embeddings(tmp_path: Path, monkeypatch):
    from sc_digger.embeddings import BackfillSummary

    cfg = _cfg(tmp_path)
    aufgerufen = {}

    def fake_embeddings(db, *, dry_run=False):
        aufgerufen["dry_run"] = dry_run
        aufgerufen["db_path"] = str(db.path)
        return BackfillSummary(queued=7, already_queued=4)

    monkeypatch.setattr("sc_digger.embeddings.backfill_embedding_jobs", fake_embeddings)
    runners = default_runners(cfg)
    msg = runners["backfill-embeddings"]()
    assert aufgerufen["dry_run"] is False
    assert aufgerufen["db_path"] == cfg["state"]["track_db_path"]
    assert msg == "Embeddings: 7 eingereiht, 4 bereits aktiv"


def test_runner_requeue(tmp_path: Path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        "sc_digger.main.run_requeue_cmd",
        lambda c: "Job-Warteschlange: 3 hängende Jobs zurückgesetzt",
    )
    runners = default_runners(cfg)
    msg = runners["requeue"]()
    assert msg == "Job-Warteschlange: 3 hängende Jobs zurückgesetzt"


def test_runner_backup(tmp_path: Path, monkeypatch):
    cfg = _cfg(tmp_path)
    aufgerufen = {}

    def fake_backup(c, *, today, notify=None):
        aufgerufen["cfg"] = c
        aufgerufen["today"] = today
        return 0

    monkeypatch.setattr("sc_digger.backup.run_backup", fake_backup)
    runners = default_runners(cfg)
    msg = runners["backup"]()
    assert aufgerufen["cfg"] is cfg
    assert aufgerufen["today"] is not None
    assert "Sicherung erfolgreich" in msg

    monkeypatch.setattr("sc_digger.backup.run_backup", lambda c, today=None, notify=None: 1)
    with pytest.raises(RuntimeError, match="Sicherung fehlgeschlagen"):
        runners["backup"]()


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


def test_runner_backfill_names(tmp_path: Path, monkeypatch):
    cfg = _cfg(tmp_path)
    aufgerufen = {}

    def fake_names(db, *, dry_run=False, limit=None):
        aufgerufen["dry_run"] = dry_run
        return {"seen": 5, "set": 4, "would_set": 0, "skipped": 1}

    monkeypatch.setattr("sc_digger.backfill.backfill_names", fake_names)
    msg = default_runners(cfg)["backfill-names"]()
    assert aufgerufen["dry_run"] is False
    assert msg == "Artist/Titel: 4 ergänzt, 1 ohne Artist im Namen"
