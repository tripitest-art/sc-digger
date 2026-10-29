"""Akzeptanztests: Workstation täglich wecken und Cue-Jobs abarbeiten (Teil 3 von #227)."""
from __future__ import annotations

from pathlib import Path

import requests

from sc_digger import gpu_wake
from sc_digger.db import JobType, TrackDB, TrackRecord, TrackStatus, enqueue_cue_job
from sc_digger.gpu_wake import count_pending_jobs, main, run_wake, wake_workstation
from sc_digger.worker import run_worker_loop

ROOT = Path(__file__).resolve().parents[2]
WAKE_URL = "http://wake.invalid:11434/api/show"


# --- Hilfen ---------------------------------------------------------------------


def _track(db: TrackDB, name: str):
    return db.upsert_track(
        TrackRecord(path=f"/inbox/{name}.wav", mtime=1.0, size=10, status=TrackStatus.INBOX)
    )


def _queue(tmp_path: Path, cues: int = 0, embeddings: int = 0, claimed: int = 0) -> Path:
    path = tmp_path / "tracks.sqlite"
    with TrackDB(path) as db:
        for i in range(cues):
            enqueue_cue_job(db, _track(db, f"c{i}"))
        for i in range(embeddings):
            db.enqueue_job(_track(db, f"e{i}").id, JobType.EMBEDDING, payload={})
        for _ in range(claimed):
            db.claim_next_job(JobType.CUES)
    return path


class Resp:
    def __init__(self, status_code: int):
        self.status_code = status_code


class Post:
    """Ersetzt requests.post; zeichnet Aufrufe auf."""

    def __init__(self, status: int = 200, error: Exception | None = None):
        self.status, self.error, self.calls = status, error, []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return Resp(self.status)


class Notes:
    def __init__(self):
        self.texts: list[str] = []

    def __call__(self, text: str) -> None:
        self.texts.append(text)


# --- Offene Jobs zählen ----------------------------------------------------------


def test_count_pending_jobs_counts_only_open_cue_jobs(tmp_path):
    # 2 Cue-Jobs offen, 1 Cue-Job schon vergeben (running), 3 Embedding-Jobs offen
    db = _queue(tmp_path, cues=3, embeddings=3, claimed=1)
    assert count_pending_jobs(db) == 2
    assert count_pending_jobs(db, "embedding") == 3


def test_count_pending_jobs_missing_db_is_zero_and_creates_nothing(tmp_path):
    path = tmp_path / "gibt-es-nicht.sqlite"
    assert count_pending_jobs(path) == 0
    assert not path.exists()


# --- Wecken ----------------------------------------------------------------------


def test_wake_workstation_posts_to_the_url_and_accepts_any_non_server_error():
    for status in (200, 404, 400):  # 404: Modell unbekannt, der PC ist trotzdem wach
        post = Post(status)
        assert wake_workstation(WAKE_URL, post=post, timeout=7.0) is True
        assert post.calls[0][0] == WAKE_URL
        assert post.calls[0][1]["timeout"] == 7.0


def test_wake_workstation_reports_failure_for_server_errors_and_network_errors():
    assert wake_workstation(WAKE_URL, post=Post(502)) is False
    assert wake_workstation(WAKE_URL, post=Post(error=requests.ConnectionError("weg"))) is False
    assert wake_workstation(WAKE_URL, post=Post(error=requests.Timeout("zu langsam"))) is False


def test_run_wake_does_not_wake_when_no_cue_job_is_open(tmp_path):
    db = _queue(tmp_path, cues=2, embeddings=2, claimed=2)
    post, notes = Post(), Notes()
    assert run_wake(db, WAKE_URL, post=post, notify=notes) == 0
    assert post.calls == []
    assert notes.texts == []


def test_run_wake_wakes_exactly_once_when_cue_jobs_are_open(tmp_path):
    db = _queue(tmp_path, cues=3)
    post, notes = Post(), Notes()
    assert run_wake(db, WAKE_URL, post=post, notify=notes) == 0
    assert [c[0] for c in post.calls] == [WAKE_URL]
    assert notes.texts == []


def test_run_wake_failure_exits_1_and_notifies_without_leaking_the_url(tmp_path):
    db = _queue(tmp_path, cues=1)
    notes = Notes()
    err = requests.ConnectionError(f"Verbindung zu {WAKE_URL} abgelehnt")
    assert run_wake(db, WAKE_URL, post=Post(error=err), notify=notes) == 1
    assert len(notes.texts) == 1
    assert "wake.invalid" not in notes.texts[0]
    assert "Workstation" in notes.texts[0]


def test_main_without_wake_url_exits_2(tmp_path, monkeypatch):
    monkeypatch.delenv("SC_DIGGER_WAKE_URL", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"state:\n  track_db_path: {_queue(tmp_path, cues=1)}\n", encoding="utf-8")
    assert main(["--config", str(cfg)]) == 2


def test_main_reads_wake_url_from_environment_and_wakes(tmp_path, monkeypatch):
    post = Post()
    monkeypatch.setenv("SC_DIGGER_WAKE_URL", WAKE_URL)
    monkeypatch.setattr(gpu_wake.requests, "post", post)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"state:\n  track_db_path: {_queue(tmp_path, cues=1)}\n", encoding="utf-8")
    assert main(["--config", str(cfg)]) == 0
    assert [c[0] for c in post.calls] == [WAKE_URL]


# --- Worker beendet sich bei leerer Queue ----------------------------------------


class Ticker:
    """Gemeinsame Uhr: Warten schiebt die Zeit vor; stoppt nach max_waits als Notbremse."""

    def __init__(self, max_waits: int = 100):
        self.now = 0.0
        self.waits: list[float] = []
        self.max_waits = max_waits
        self._set = False

    def clock(self) -> float:
        return self.now

    def is_set(self) -> bool:
        return self._set

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        self.now += seconds
        if len(self.waits) >= self.max_waits:
            self._set = True
        return self._set


class Client:
    def __init__(self, jobs=None, claim_error=None):
        self.jobs = list(jobs or [])
        self.claim_error = claim_error
        self.claims = 0
        self.completed: dict = {}

    def claim_job(self, job_type=None):
        self.claims += 1
        if self.claim_error is not None:
            raise self.claim_error
        return self.jobs.pop(0) if self.jobs else None

    def heartbeat(self, job_id):
        return True

    def complete_job(self, job_id, status, result=None, error=None):
        self.completed[job_id] = status
        return True


def test_worker_loop_default_runs_until_stopped():
    t = Ticker(max_waits=5)
    run_worker_loop(Client(), poll_interval=20.0, stop_event=t)
    assert len(t.waits) == 5  # ohne idle_exit_s gibt es kein Eigen-Ende


def test_worker_loop_exits_after_idle_time_with_empty_queue():
    t = Ticker()
    client = Client()
    handled = run_worker_loop(
        client, poll_interval=20.0, stop_event=t, idle_exit_s=60.0, clock=t.clock,
        handler_map={},
    )
    assert handled == 0
    assert not t.is_set(), "Notbremse hat gegriffen: die Schleife endet nicht von selbst"
    assert 60.0 <= t.now <= 80.0  # nicht früher als 60 s, höchstens ein Abfragetakt später


def test_worker_loop_idle_timer_restarts_after_each_job():
    t = Ticker()
    jobs = [{"id": 1, "job_type": "cues", "payload": {}}, {"id": 2, "job_type": "cues", "payload": {}}]
    client = Client(jobs)
    handled = run_worker_loop(
        client, poll_interval=20.0, stop_event=t, idle_exit_s=60.0, clock=t.clock,
        handler_map={"cues": lambda payload: {"ok": True}},
    )
    assert handled == 2
    assert client.completed == {1: "completed", 2: "completed"}
    assert not t.is_set()
    assert t.now >= 60.0


def test_worker_loop_server_down_counts_as_idle_and_ends():
    t = Ticker()
    client = Client(claim_error=requests.ConnectionError("Server weg"))
    assert run_worker_loop(
        client, poll_interval=20.0, stop_event=t, idle_exit_s=60.0, clock=t.clock, handler_map={},
    ) == 0
    assert not t.is_set()


def test_worker_cli_accepts_idle_exit_option():
    from sc_digger import worker

    args = worker._build_parser().parse_args(["--api-url", "http://x", "--idle-exit", "300"])
    assert args.idle_exit == 300.0
    assert worker._build_parser().parse_args(["--api-url", "http://x"]).idle_exit is None


# --- Einrichtungsdateien ----------------------------------------------------------


def _read(rel: str) -> str:
    p = ROOT / rel
    assert p.is_file(), f"{rel} fehlt"
    return p.read_text(encoding="utf-8")


def test_workstation_unit_runs_only_cue_jobs_and_ends_with_the_queue():
    unit = _read("deploy/workstation/sc-digger-gpu-worker.service")
    exec_line = next(l for l in unit.splitlines() if l.startswith("ExecStart="))
    assert "-m sc_digger.worker" in exec_line
    assert "--job-type cues" in exec_line  # Embedding-Jobs hat dieser Runner nicht
    assert "--idle-exit" in exec_line
    assert "EnvironmentFile=" in unit
    assert "WantedBy=multi-user.target" in unit
    assert "Restart=always" not in unit  # sonst fährt der Idle-Shutdown nie durch


def test_workstation_unit_and_scripts_contain_no_credentials_or_addresses():
    for rel in (
        "deploy/workstation/sc-digger-gpu-worker.service",
        "deploy/sc-digger-gpu-wake.service",
        "deploy/sc-digger-gpu-wake.timer",
    ):
        text = _read(rel)
        assert "192.168." not in text, rel
        assert "http://" not in text, rel  # Adressen kommen aus der Env-Datei


def test_wake_service_and_timer_run_gpu_wake_inside_the_container_once_a_day():
    service = _read("deploy/sc-digger-gpu-wake.service")
    timer = _read("deploy/sc-digger-gpu-wake.timer")
    exec_line = next(l for l in service.splitlines() if l.startswith("ExecStart="))
    assert "docker compose exec -T" in exec_line
    assert "-e SC_DIGGER_WAKE_URL" in exec_line  # reicht die Adresse aus der Env-Datei in den Container
    assert "EnvironmentFile=" in service  # die Adresse der Workstation steht nicht im Repo
    assert "sc_digger.gpu_wake" in exec_line
    assert "Type=oneshot" in service
    assert "OnCalendar=" in timer and "WantedBy=timers.target" in timer


def test_betrieb_documents_the_gpu_worker_setup():
    text = _read("BETRIEB.md")
    assert "sc-digger-gpu-worker" in text
    assert "sc-digger-gpu-wake" in text
    assert "ollama-idle-shutdown" in text  # die Zeile fürs Idle-Skript steht in der Anleitung
