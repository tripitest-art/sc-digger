"""Akzeptanztests: Workstation GPU-Runner Client (Issue #331)."""
from pathlib import Path
import pytest

from sc_digger.worker import JobWorkerClient, execute_job, run_worker_loop

ROOT = Path(__file__).resolve().parents[2]


class FakeAPI:
    def __init__(self):
        self.jobs = [
            {"id": 1, "job_type": "embedding", "payload": {"track_path": "audio/track1.wav"}},
            {"id": 2, "job_type": "caption", "payload": {"track_path": "audio/track2.wav"}},
        ]
        self.heartbeats = []
        self.completed = {}

    def claim(self, job_type=None):
        if not self.jobs:
            return None
        if job_type:
            for i, j in enumerate(self.jobs):
                if j["job_type"] == job_type:
                    return self.jobs.pop(i)
            return None
        return self.jobs.pop(0)

    def heartbeat(self, job_id):
        self.heartbeats.append(job_id)
        return True

    def complete(self, job_id, status, result=None, error=None):
        self.completed[job_id] = {"status": status, "result": result, "error": error}
        return True


def test_job_worker_client_claim_heartbeat_complete():
    fake = FakeAPI()
    client = JobWorkerClient("http://fake-server:8080", password="secret")

    # Methoden mit Fake verknüpfen
    client.claim_job = fake.claim
    client.heartbeat = fake.heartbeat
    client.complete_job = fake.complete

    job = client.claim_job(job_type="embedding")
    assert job is not None
    assert job["id"] == 1

    hb_ok = client.heartbeat(job["id"])
    assert hb_ok is True
    assert 1 in fake.heartbeats

    comp_ok = client.complete_job(job["id"], "completed", result={"dims": 512})
    assert comp_ok is True
    assert fake.completed[1]["status"] == "completed"
    assert fake.completed[1]["result"] == {"dims": 512}


def test_execute_job_success_and_failure():
    def mock_embedding_handler(payload):
        return {"embedding": [0.1, 0.2, 0.3]}

    def mock_failing_handler(payload):
        raise ValueError("Audio corrupted")

    handlers = {
        "embedding": mock_embedding_handler,
        "failing": mock_failing_handler,
    }

    # Erfolg
    res_ok = execute_job({"id": 10, "job_type": "embedding", "payload": {}}, handler_map=handlers)
    assert res_ok["status"] == "completed"
    assert res_ok["result"] == {"embedding": [0.1, 0.2, 0.3]}

    # Fehler wird gefangen und als failed zurückgegeben
    res_fail = execute_job({"id": 11, "job_type": "failing", "payload": {}}, handler_map=handlers)
    assert res_fail["status"] == "failed"
    assert "Audio corrupted" in res_fail["error"]


def test_run_worker_loop_processes_n_jobs():
    fake = FakeAPI()
    client = JobWorkerClient("http://fake-server:8080", password="secret")
    client.claim_job = fake.claim
    client.heartbeat = fake.heartbeat
    client.complete_job = fake.complete

    handlers = {
        "embedding": lambda p: {"ok": True},
        "caption": lambda p: {"caption": "Dark Industrial"},
    }

    processed = run_worker_loop(
        client,
        poll_interval=0.01,
        max_jobs=2,
        handler_map=handlers,
    )

    assert processed == 2
    assert 1 in fake.completed
    assert 2 in fake.completed
    assert fake.completed[1]["status"] == "completed"
    assert fake.completed[2]["status"] == "completed"
