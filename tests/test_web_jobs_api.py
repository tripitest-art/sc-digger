"""Eigene Tests für die Job-Queue-REST-API im Web-Dashboard (Issue #330).

Deckt die drei Worker-Routen ``/api/jobs/claim``, ``/api/jobs/{id}/heartbeat`` und
``/api/jobs/{id}/complete`` sowie die Schreibfunktionen in ``web.database`` ab: fehlende
Track-DB, Filterung nach ``job_type``, Fehler-Abschluss mit ``error``-Feld, ungültiges JSON
und ein Heartbeat auf einen noch nicht gestarteten Job.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sc_digger.db import JobStatus, JobType, TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.database import claim_job_for_worker, finish_job, heartbeat_job

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-330"
AUTH = ("sc", PASSWORD)


def make_cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def client_for(tmp_path: Path) -> TestClient:
    return TestClient(create_app(make_cfg(tmp_path), password=PASSWORD))


def seed_jobs(tmp_path: Path, *job_types: JobType) -> list[int]:
    """Legt je Job-Typ einen anstehenden Job an und liefert die IDs in Reihenfolge."""
    job_ids: list[int] = []
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        track = db.upsert_track(TrackRecord(path="audio/test.wav", mtime=1.0, size=100))
        for job_type in job_types:
            job = db.enqueue_job(track.id, job_type, payload={"typ": job_type.value})
            job_ids.append(int(job.id))
    return job_ids


def test_claim_filtert_nach_job_type(tmp_path: Path):
    embedding_id, caption_id = seed_jobs(tmp_path, JobType.EMBEDDING, JobType.CAPTION)
    client = client_for(tmp_path)

    caption = client.post("/api/jobs/claim", json={"job_type": "caption"}, auth=AUTH)
    assert caption.status_code == 200
    assert caption.json()["job"]["id"] == caption_id
    assert caption.json()["job"]["track_id"] == 1

    embedding = client.post("/api/jobs/claim", json={"job_type": "embedding"}, auth=AUTH)
    assert embedding.status_code == 200
    assert embedding.json()["job"]["id"] == embedding_id

    # Beide Jobs laufen jetzt; ein Claim ohne Typ findet nichts mehr.
    empty = client.post("/api/jobs/claim", json={}, auth=AUTH)
    assert empty.status_code == 200
    assert empty.json() == {"ok": True, "job": None}


def test_claim_ohne_body_nimmt_naechsten_job(tmp_path: Path):
    job_id, = seed_jobs(tmp_path, JobType.FINGERPRINT)
    client = client_for(tmp_path)

    res = client.post("/api/jobs/claim", auth=AUTH)
    assert res.status_code == 200
    assert res.json()["job"]["id"] == job_id


def test_heartbeat_auf_pending_job_ist_404(tmp_path: Path):
    job_id, = seed_jobs(tmp_path, JobType.EMBEDDING)
    client = client_for(tmp_path)

    res = client.post(f"/api/jobs/{job_id}/heartbeat", auth=AUTH)
    assert res.status_code == 404
    assert res.json()["ok"] is False

    # Nach dem Claim ist der Job 'running' und der Heartbeat greift.
    assert client.post("/api/jobs/claim", auth=AUTH).json()["job"]["id"] == job_id
    ok = client.post(f"/api/jobs/{job_id}/heartbeat", auth=AUTH)
    assert ok.status_code == 200
    assert ok.json() == {"ok": True}


def test_complete_failed_speichert_error(tmp_path: Path):
    job_id, = seed_jobs(tmp_path, JobType.CAPTION)
    client = client_for(tmp_path)
    client.post("/api/jobs/claim", auth=AUTH)

    res = client.post(
        f"/api/jobs/{job_id}/complete",
        json={"status": "failed", "error": "ffmpeg fehlt"},
        auth=AUTH,
    )
    assert res.status_code == 200
    assert res.json() == {"ok": True}

    with TrackDB(tmp_path / "tracks.sqlite") as db:
        job = db.get_job_by_id(job_id)
        assert job is not None
        assert job.status == JobStatus.FAILED.value
        assert job.error == "ffmpeg fehlt"
        assert job.result is None


def test_complete_unbekannter_job_ist_404(tmp_path: Path):
    seed_jobs(tmp_path, JobType.EMBEDDING)
    client = client_for(tmp_path)

    res = client.post(
        "/api/jobs/999999/complete",
        json={"status": "completed", "result": {}},
        auth=AUTH,
    )
    assert res.status_code == 404
    assert res.json()["ok"] is False


@pytest.mark.parametrize("body", [b"{kein json", b"[1, 2]"])
def test_claim_mit_ungueltigem_json_ist_400(tmp_path: Path, body: bytes):
    seed_jobs(tmp_path, JobType.EMBEDDING)
    client = client_for(tmp_path)

    res = client.post(
        "/api/jobs/claim",
        content=body,
        headers={"content-type": "application/json"},
        auth=AUTH,
    )
    assert res.status_code == 400
    assert res.json()["ok"] is False


def test_fehlende_datenbank_wird_nicht_angelegt(tmp_path: Path):
    db_path = tmp_path / "fehlt.sqlite"
    client = client_for(tmp_path)

    claim = client.post("/api/jobs/claim", auth=AUTH)
    assert claim.status_code == 200
    assert claim.json() == {"ok": True, "job": None}
    assert not db_path.exists()

    assert client.post("/api/jobs/1/heartbeat", auth=AUTH).status_code == 404
    assert client.post(
        "/api/jobs/1/complete", json={"status": "completed"}, auth=AUTH
    ).status_code == 404
    assert not db_path.exists()


def test_funktionen_werfen_nie_bei_fehlender_datei(tmp_path: Path):
    fehlend = tmp_path / "fehlt.sqlite"
    assert claim_job_for_worker(fehlend) is None
    assert heartbeat_job(fehlend, 1) is False
    assert finish_job(fehlend, 1, "completed") is False
    assert finish_job(fehlend, 1, "kaputt") is False
    assert not fehlend.exists()
