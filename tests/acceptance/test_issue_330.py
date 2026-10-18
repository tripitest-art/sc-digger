"""Akzeptanztests: Job-Queue REST-API für externe Worker (Issue #330)."""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from sc_digger.db import JobStatus, JobType, TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "test-secret-workstation"


def make_test_cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def test_job_queue_api_claim_and_complete_lifecycle(tmp_path: Path):
    cfg = make_test_cfg(tmp_path)
    track_db_path = cfg["state"]["track_db_path"]

    # 1. Job in DB vorbereiten
    with TrackDB(track_db_path) as db:
        track = db.upsert_track(TrackRecord(path="audio/test.wav", mtime=1.0, size=100))
        job = db.enqueue_job(track.id, JobType.EMBEDDING, payload={"model": "clap"})
        job_id = job.id

    client = TestClient(create_app(cfg, password=PASSWORD))
    auth = ("sc", PASSWORD)

    # 2. Claim ohne Auth -> 401
    unauth = client.post("/api/jobs/claim", json={"job_type": "embedding"})
    assert unauth.status_code == 401

    # 3. Claim mit Auth -> Job erhalten
    res = client.post("/api/jobs/claim", json={"job_type": "embedding"}, auth=auth)
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["job"]["id"] == job_id
    assert data["job"]["status"] == JobStatus.RUNNING.value
    assert data["job"]["payload"] == {"model": "clap"}

    # 4. Erneuter Claim -> Keine Jobs mehr da
    empty_res = client.post("/api/jobs/claim", json={"job_type": "embedding"}, auth=auth)
    assert empty_res.status_code == 200
    assert empty_res.json()["job"] is None

    # 5. Heartbeat für laufenden Job -> 200
    hb_res = client.post(f"/api/jobs/{job_id}/heartbeat", auth=auth)
    assert hb_res.status_code == 200
    assert hb_res.json()["ok"] is True

    # 6. Heartbeat für unbekannten Job -> 404
    hb_unknown = client.post("/api/jobs/999999/heartbeat", auth=auth)
    assert hb_unknown.status_code == 404

    # 7. Complete für Job -> 200
    comp_res = client.post(
        f"/api/jobs/{job_id}/complete",
        json={"status": "completed", "result": {"embedding_dim": 512}},
        auth=auth,
    )
    assert comp_res.status_code == 200
    assert comp_res.json()["ok"] is True

    # 8. Verifizieren in DB
    with TrackDB(track_db_path) as db:
        finished = db.get_job_by_id(job_id)
        assert finished is not None
        assert finished.status == JobStatus.COMPLETED.value
        assert finished.result == {"embedding_dim": 512}


def test_job_queue_api_invalid_payloads(tmp_path: Path):
    cfg = make_test_cfg(tmp_path)
    client = TestClient(create_app(cfg, password=PASSWORD))
    auth = ("sc", PASSWORD)

    # Ungültiger Status bei complete -> 422
    bad_status = client.post(
        "/api/jobs/1/complete",
        json={"status": "invalid_status"},
        auth=auth,
    )
    assert bad_status.status_code == 422
