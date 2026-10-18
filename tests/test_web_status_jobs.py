"""Eigene Tests zum Job-Queue-Monitor der Statusseite (Issue #336).

Ergänzt die Akzeptanztests um die Ränder: fehlende jobs-Tabelle, defekte SQLite-Datei und
die Heartbeat-Berechnung (frisch vs. veraltet).
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sc_digger.models import Config
from sc_digger.web.status import JobQueueSummary, _read_job_summary, collect_status

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 18, 14, 0, 0, tzinfo=timezone.utc)


def make_cfg(tmp_path, *, create_track_db: bool = False):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    if create_track_db:
        sqlite3.connect(cfg["state"]["track_db_path"]).close()
    return cfg


def _create_jobs_table(db_path, rows):
    """Minimale jobs-Tabelle; status und updated_at sind alles, was der Monitor liest."""
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT, updated_at TEXT)"
        )
        db.executemany("INSERT INTO jobs (status, updated_at) VALUES (?, ?)", rows)
        db.commit()


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def test_missing_jobs_table_gives_empty_summary(tmp_path):
    cfg = make_cfg(tmp_path, create_track_db=True)
    snap = collect_status(cfg, now=NOW)
    assert snap.job_summary == JobQueueSummary()
    assert snap.job_summary.to_dict() == {
        "pending": 0, "running": 0, "completed": 0, "failed": 0,
        "last_heartbeat_at": None, "workstation_active": False,
    }


def test_missing_track_db_is_not_created(tmp_path):
    cfg = make_cfg(tmp_path)
    snap = collect_status(cfg, now=NOW)
    assert snap.job_summary == JobQueueSummary()
    assert not Path(cfg["state"]["track_db_path"]).exists()


def test_broken_sqlite_file_gives_empty_summary(tmp_path):
    cfg = make_cfg(tmp_path)
    Path(cfg["state"]["track_db_path"]).write_bytes(b"kein sqlite, nur muell")
    snap = collect_status(cfg, now=NOW)
    assert snap.job_summary == JobQueueSummary()


def test_workstation_active_for_fresh_running_heartbeat(tmp_path):
    cfg = make_cfg(tmp_path)
    _create_jobs_table(cfg["state"]["track_db_path"], [
        ("pending", _stamp(NOW - timedelta(minutes=10))),
        ("running", _stamp(NOW - timedelta(seconds=60))),
        ("completed", _stamp(NOW - timedelta(minutes=2))),
    ])
    summary = _read_job_summary(cfg["state"]["track_db_path"], NOW)
    assert summary.pending == 1
    assert summary.running == 1
    assert summary.completed == 1
    assert summary.workstation_active is True
    assert summary.last_heartbeat_at == _stamp(NOW - timedelta(seconds=60))


def test_workstation_standby_for_stale_running_heartbeat(tmp_path):
    cfg = make_cfg(tmp_path)
    _create_jobs_table(cfg["state"]["track_db_path"], [
        ("running", _stamp(NOW - timedelta(seconds=301))),
    ])
    summary = _read_job_summary(cfg["state"]["track_db_path"], NOW)
    assert summary.running == 1
    assert summary.workstation_active is False
    assert summary.last_heartbeat_at == _stamp(NOW - timedelta(seconds=301))


def test_fresh_completed_job_does_not_mark_workstation_active(tmp_path):
    cfg = make_cfg(tmp_path)
    _create_jobs_table(cfg["state"]["track_db_path"], [
        ("completed", _stamp(NOW - timedelta(seconds=5))),
    ])
    summary = _read_job_summary(cfg["state"]["track_db_path"], NOW)
    assert summary.workstation_active is False
    assert summary.last_heartbeat_at == _stamp(NOW - timedelta(seconds=5))


def test_last_heartbeat_prefers_newest_timestamp(tmp_path):
    cfg = make_cfg(tmp_path)
    _create_jobs_table(cfg["state"]["track_db_path"], [
        ("completed", _stamp(NOW - timedelta(minutes=30))),
        ("running", _stamp(NOW - timedelta(minutes=1))),
        ("completed", _stamp(NOW - timedelta(minutes=5))),
    ])
    summary = _read_job_summary(cfg["state"]["track_db_path"], NOW)
    assert summary.last_heartbeat_at == _stamp(NOW - timedelta(minutes=1))
