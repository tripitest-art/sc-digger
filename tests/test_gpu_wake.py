"""Eigene Tests zu sc_digger/gpu_wake.py und dem Leerlauf-Ende des Workers (Issue #409)."""
from __future__ import annotations

import json
import sqlite3
import time

import requests

from sc_digger import gpu_wake
from sc_digger.db import TrackDB, TrackRecord, TrackStatus, enqueue_cue_job
from sc_digger.gpu_wake import count_pending_jobs, main, run_wake, wake_workstation
from sc_digger.worker import run_worker_loop

WAKE_URL = "http://wake.invalid:11434/api/show"


class Resp:
    def __init__(self, status_code):
        self.status_code = status_code


def _queue(tmp_path, cues=1):
    path = tmp_path / "tracks.sqlite"
    with TrackDB(path) as db:
        for i in range(cues):
            t = db.upsert_track(
                TrackRecord(path=f"/inbox/{i}.wav", mtime=1.0, size=1, status=TrackStatus.INBOX)
            )
            enqueue_cue_job(db, t)
    return path


def test_count_pending_jobs_broken_db_is_zero_without_path_in_log(tmp_path, caplog):
    path = tmp_path / "kaputt-geheim.sqlite"
    path.write_bytes(b"das ist keine sqlite-datei" * 100)
    with caplog.at_level("WARNING", logger="sc_digger.gpu_wake"):
        assert count_pending_jobs(path) == 0
    assert "kaputt-geheim" not in caplog.text
    assert caplog.records, "Warnung fehlt"


def test_count_pending_jobs_db_without_jobs_table_is_zero(tmp_path):
    path = tmp_path / "leer.sqlite"
    sqlite3.connect(path).close()
    assert count_pending_jobs(path) == 0


def test_count_pending_jobs_does_not_write(tmp_path):
    path = _queue(tmp_path, cues=2)
    before = path.read_bytes()
    assert count_pending_jobs(path) == 2
    assert path.read_bytes() == before


def test_wake_workstation_sends_wake_payload_and_does_not_log_the_url(caplog):
    seen = {}

    def post(url, **kwargs):
        seen.update(kwargs)
        raise requests.ConnectionError(f"Verbindung zu {url} abgelehnt")

    with caplog.at_level("DEBUG"):
        assert wake_workstation(WAKE_URL, post=post) is False
    assert seen["json"] == {"name": "wake"}
    assert "wake.invalid" not in caplog.text


def test_wake_workstation_default_timeout_covers_cold_start():
    seen = {}

    def post(url, **kwargs):
        seen.update(kwargs)
        return Resp(200)

    assert wake_workstation(WAKE_URL, post=post) is True
    assert seen["timeout"] >= 60.0


def test_run_wake_works_without_notify_on_failure(tmp_path):
    assert run_wake(_queue(tmp_path), WAKE_URL, post=lambda url, **kw: Resp(503)) == 1


def test_main_failure_sends_telegram_via_telegram_call(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("SC_DIGGER_WAKE_URL", WAKE_URL)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(gpu_wake.requests, "post", lambda url, **kw: Resp(502))
    monkeypatch.setattr(
        "sc_digger.output.telegram_call", lambda token, method, **kw: calls.append((method, kw))
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"state:\n  track_db_path: {_queue(tmp_path)}\n", encoding="utf-8")
    assert main(["--config", str(cfg)]) == 1
    assert len(calls) == 1
    method, kw = calls[0]
    assert method == "sendMessage"
    assert kw["json"]["chat_id"] == "42"
    assert "Workstation" in kw["json"]["text"]
    assert "wake.invalid" not in kw["json"]["text"]


def test_main_telegram_error_is_not_fatal(tmp_path, monkeypatch):
    from sc_digger.output import TelegramError

    def boom(token, method, **kw):
        raise TelegramError("sendMessage: Telegram 400: kaputt")

    monkeypatch.setenv("SC_DIGGER_WAKE_URL", WAKE_URL)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(gpu_wake.requests, "post", lambda url, **kw: Resp(502))
    monkeypatch.setattr("sc_digger.output.telegram_call", boom)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"state:\n  track_db_path: {_queue(tmp_path)}\n", encoding="utf-8")
    assert main(["--config", str(cfg)]) == 1


def test_main_without_telegram_credentials_still_returns_1(tmp_path, monkeypatch):
    monkeypatch.setenv("SC_DIGGER_WAKE_URL", WAKE_URL)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(gpu_wake.requests, "post", lambda url, **kw: Resp(502))
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"state:\n  track_db_path: {_queue(tmp_path)}\n", encoding="utf-8")
    assert main(["--config", str(cfg)]) == 1


def test_main_wake_url_option_beats_environment(tmp_path, monkeypatch):
    urls = []
    monkeypatch.setenv("SC_DIGGER_WAKE_URL", "http://aus-env.invalid/x")
    monkeypatch.setattr(gpu_wake.requests, "post", lambda url, **kw: urls.append(url) or Resp(200))
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"state:\n  track_db_path: {_queue(tmp_path)}\n", encoding="utf-8")
    assert main(["--config", str(cfg), "--wake-url", WAKE_URL]) == 0
    assert urls == [WAKE_URL]


# --- Worker: Leerlauf-Ende ---------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Stop:
    """stop_event-Ersatz: Warten schiebt die Uhr."""

    def __init__(self, clock):
        self.clock = clock

    def is_set(self):
        return False

    def wait(self, seconds):
        self.clock.now += seconds
        return False


def test_idle_exit_not_before_deadline_and_job_duration_does_not_count():
    clock = Clock()

    class Client:
        def __init__(self):
            self.jobs = [{"id": 1, "job_type": "cues", "payload": {}}]
            self.claim_times = []

        def claim_job(self, job_type=None):
            self.claim_times.append(clock.now)
            return self.jobs.pop(0) if self.jobs else None

        def heartbeat(self, job_id):
            return True

        def complete_job(self, job_id, status, result=None, error=None):
            return True

    def slow(payload):
        clock.now += 1000.0  # ein langer Job darf die Leerlauf-Frist nicht aufbrauchen
        return {}

    client = Client()
    handled = run_worker_loop(
        client, poll_interval=20.0, stop_event=Stop(clock), idle_exit_s=60.0, clock=clock,
        handler_map={"cues": slow},
    )
    assert handled == 1
    # Nach dem Job (t=1000) wird noch bis t=1060 weitergefragt.
    assert clock.now >= 1060.0
    assert len(client.claim_times) >= 4


def test_idle_exit_zero_ends_after_first_empty_poll():
    clock = Clock()

    class Client:
        def claim_job(self, job_type=None):
            return None

    assert run_worker_loop(
        Client(), poll_interval=20.0, stop_event=Stop(clock), idle_exit_s=0.0, clock=clock,
        handler_map={},
    ) == 0
    assert clock.now == 20.0


def test_count_pending_jobs_none_zaehlt_nur_wecktypen(tmp_path):
    from sc_digger.db import JobType

    path = _queue(tmp_path, cues=1)
    with TrackDB(path) as db:
        for i, typ in enumerate((JobType.EMBEDDING, JobType.FINGERPRINT)):
            t = db.upsert_track(
                TrackRecord(path=f"/inbox/x{i}.wav", mtime=1.0, size=1, status=TrackStatus.INBOX)
            )
            db.enqueue_job(t.id, typ, payload={})
    assert count_pending_jobs(path) == 1
    assert count_pending_jobs(path, "fingerprint") == 1
    assert count_pending_jobs(path, None) == 2


def test_run_wake_fehlermeldung_nennt_jobs_ohne_url(tmp_path):
    path = _queue(tmp_path, cues=2)
    notes = []
    code = run_wake(path, WAKE_URL, post=lambda url, **kw: Resp(503), notify=notes.append)
    assert code == 1
    assert notes == ["⚠️ Workstation konnte nicht geweckt werden, 2 Job(s) bleiben offen."]
    assert "wake.invalid" not in notes[0]


# --- Cooldown und Zustandsdatei (Issue #757) ---------------------------------------


def test_main_writes_state_file_next_to_track_db(tmp_path, monkeypatch):
    urls = []
    monkeypatch.setattr(gpu_wake.requests, "post", lambda url, **kw: urls.append(url) or Resp(200))
    db = _queue(tmp_path)
    vorher = time.time()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"state:\n  track_db_path: {db}\n", encoding="utf-8")
    assert main(["--config", str(cfg), "--wake-url", WAKE_URL]) == 0
    state = db.with_name("gpu_wake_state.json")
    data = json.loads(state.read_text(encoding="utf-8"))
    assert set(data) == {"last_attempt"}
    assert vorher <= data["last_attempt"] <= time.time()
    # Zweiter Lauf direkt danach: Cooldown greift, kein zweiter Weckruf.
    assert main(["--config", str(cfg), "--wake-url", WAKE_URL]) == 0
    assert urls == [WAKE_URL]


def test_run_wake_without_state_path_creates_no_file(tmp_path):
    db = _queue(tmp_path)
    assert run_wake(db, WAKE_URL, post=lambda url, **kw: Resp(200)) == 0
    assert not list(tmp_path.glob("*.json*"))


def test_run_wake_cooldown_ends_exactly_at_cooldown_s(tmp_path):
    rufe = []
    state = tmp_path / "wake.json"
    state.write_text(json.dumps({"last_attempt": 1000.0}), encoding="utf-8")

    def post(url, **kw):
        rufe.append(url)
        return Resp(200)

    run_wake(_queue(tmp_path), WAKE_URL, post=post, state_path=state, now=lambda: 1099.0,
             cooldown_s=100.0)
    assert rufe == []
    run_wake(_queue(tmp_path), WAKE_URL, post=post, state_path=state, now=lambda: 1100.0,
             cooldown_s=100.0)
    assert rufe == [WAKE_URL]
    assert json.loads(state.read_text(encoding="utf-8"))["last_attempt"] == 1100.0


def test_run_wake_cooldown_logs_info_without_alarm(tmp_path, caplog):
    meldungen = []
    state = tmp_path / "wake.json"
    state.write_text(json.dumps({"last_attempt": 1000.0}), encoding="utf-8")
    with caplog.at_level("INFO", logger="sc_digger.gpu_wake"):
        assert run_wake(_queue(tmp_path), WAKE_URL, post=lambda url, **kw: Resp(503),
                        notify=meldungen.append, state_path=state, now=lambda: 1010.0) == 0
    assert meldungen == []
    assert any("Cooldown" in r.getMessage() and r.levelname == "INFO" for r in caplog.records)


def test_run_wake_state_with_wrong_shape_still_wakes(tmp_path):
    rufe = []
    state = tmp_path / "wake.json"
    for inhalt in ('{"last_attempt": "gestern"}', "[1, 2]", "{}", '{"last_attempt": true}'):
        state.write_text(inhalt, encoding="utf-8")
        assert run_wake(_queue(tmp_path), WAKE_URL, post=lambda url, **kw: rufe.append(url) or Resp(200),
                        state_path=state, now=lambda: 5000.0) == 0
    assert len(rufe) == 4


def test_run_wake_write_error_still_wakes(tmp_path, caplog):
    rufe = []
    # Elternordner fehlt: Schreiben scheitert, der Weckversuch läuft trotzdem.
    state = tmp_path / "fehlt" / "wake.json"
    with caplog.at_level("WARNING", logger="sc_digger.gpu_wake"):
        assert run_wake(_queue(tmp_path), WAKE_URL,
                        post=lambda url, **kw: rufe.append(url) or Resp(200),
                        state_path=state, now=lambda: 5000.0) == 0
    assert rufe == [WAKE_URL]
    assert not state.exists()
    assert any(r.levelname == "WARNING" for r in caplog.records)
