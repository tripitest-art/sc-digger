"""Akzeptanztests: Workstation- & Job-Queue-Monitor auf der Statusseite (Issue #335)."""
from datetime import datetime, timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from sc_digger.db import JobStatus, JobType, TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.status import collect_status

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "test-status-jobs"
NOW = datetime(2026, 10, 18, 14, 0, 0, tzinfo=timezone.utc)


def make_test_cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def test_collect_status_reads_job_summary(tmp_path: Path):
    cfg = make_test_cfg(tmp_path)
    db_path = cfg["state"]["track_db_path"]

    with TrackDB(db_path) as db:
        track = db.upsert_track(TrackRecord(path="audio/test.wav", mtime=1.0, size=10))
        # 1. Pending Job
        db.enqueue_job(track.id, JobType.EMBEDDING)
        # 2. Running Job
        j_run = db.enqueue_job(track.id, JobType.CAPTION)
        db.claim_next_job(JobType.CAPTION)
        # 3. Completed Job
        j_done = db.enqueue_job(track.id, JobType.FINGERPRINT)
        claimed = db.claim_next_job(JobType.FINGERPRINT)
        db.update_job(claimed.id, JobStatus.COMPLETED, result={"ok": True})

    # Abruf Status-Snapshot
    snap = collect_status(cfg, now=NOW)
    assert snap.job_summary is not None
    assert snap.job_summary.pending == 1
    assert snap.job_summary.running == 1
    assert snap.job_summary.completed == 1
    assert snap.job_summary.failed == 0

    # JSON-Serialisierung
    data = snap.to_dict()
    assert "job_summary" in data
    assert data["job_summary"]["pending"] == 1


def test_status_page_renders_job_card(tmp_path: Path):
    cfg = make_test_cfg(tmp_path)
    db_path = cfg["state"]["track_db_path"]
    with TrackDB(db_path) as db:
        t = db.upsert_track(TrackRecord(path="audio/test.wav", mtime=1.0, size=10))
        db.enqueue_job(t.id, JobType.EMBEDDING)

    client = TestClient(create_app(cfg, password=PASSWORD))
    auth = ("sc", PASSWORD)

    # HTML-Seite prüfen
    resp = client.get("/", auth=auth)
    assert resp.status_code == 200
    assert "KI-Workstation &amp; Job-Queue" in resp.text or "KI-Workstation" in resp.text
    assert "Anstehend" in resp.text

    # JSON-API prüfen
    api_resp = client.get("/api/status", auth=auth)
    assert api_resp.status_code == 200
    api_data = api_resp.json()
    assert "job_summary" in api_data
    assert api_data["job_summary"]["pending"] == 1
