"""Akzeptanztests: Workstation rechnet und weckt auch für Embedding-Jobs (Issue <N>)."""
from __future__ import annotations

from pathlib import Path

import sc_digger.embeddings as emb
import sc_digger.worker as worker
from sc_digger.db import JobType, TrackDB, TrackRecord, TrackStatus
from sc_digger.gpu_wake import WAKE_JOB_TYPES, count_pending_jobs, run_wake
from sc_digger.models import Config
from sc_digger.web.workstation import start_run

ROOT = Path(__file__).resolve().parents[2]
WAKE_URL = "http://wake.invalid/api/show"


class Resp:
    status_code = 200


class Post:
    def __init__(self):
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        return Resp()


def _queue(tmp_path: Path, *, cues=0, embeddings=0, other=0, claimed_embeddings=0) -> Path:
    path = tmp_path / "tracks.sqlite"
    with TrackDB(path) as db:
        def track(name):
            return db.upsert_track(TrackRecord(path=f"/inbox/{name}.wav", mtime=1.0, size=1,
                                               status=TrackStatus.INBOX)).id
        for i in range(cues):
            db.enqueue_job(track(f"c{i}"), JobType.CUES, payload={})
        for i in range(embeddings):
            db.enqueue_job(track(f"e{i}"), JobType.EMBEDDING, payload={})
        for i in range(other):
            db.enqueue_job(track(f"f{i}"), JobType.FINGERPRINT, payload={})
        for _ in range(claimed_embeddings):
            db.claim_next_job(JobType.EMBEDDING)
    return path


def test_weck_typen():
    assert WAKE_JOB_TYPES == ("cues", "embedding")


def test_zaehlt_cues_und_embeddings_aber_keine_anderen_typen(tmp_path):
    db = _queue(tmp_path, cues=2, embeddings=3, other=4, claimed_embeddings=1)
    assert count_pending_jobs(db) == 2
    assert count_pending_jobs(db, "embedding") == 2
    assert count_pending_jobs(db, None) == 4
    assert count_pending_jobs(tmp_path / "fehlt.sqlite", None) == 0
    assert not (tmp_path / "fehlt.sqlite").exists()


def test_embedding_jobs_allein_wecken_die_workstation(tmp_path):
    db = _queue(tmp_path, embeddings=2)
    post = Post()
    assert run_wake(db, WAKE_URL, post=post) == 0
    assert post.calls == [WAKE_URL]


def test_ohne_offene_jobs_bleibt_sie_aus(tmp_path):
    db = _queue(tmp_path, embeddings=1, other=2, claimed_embeddings=1)
    post = Post()
    assert run_wake(db, WAKE_URL, post=post) == 0
    assert post.calls == []


def test_dashboard_knopf_zaehlt_embeddings_mit(tmp_path, monkeypatch):
    _queue(tmp_path, embeddings=2)
    monkeypatch.setenv("SC_DIGGER_WAKE_URL", WAKE_URL)
    cfg = Config(raw={
        "state": {"db_path": str(tmp_path / "seen.sqlite"),
                  "track_db_path": str(tmp_path / "tracks.sqlite"),
                  "gpu_wake_state": str(tmp_path / "gpu_wake.json")},
        "gpu_wake": {"cooldown_s": 300},
    })
    post = Post()
    status, body = start_run(cfg, post=post)
    assert status == 202
    assert body["pending"] == 2
    assert post.calls == [WAKE_URL]


def test_unit_holt_alle_typen():
    unit = (ROOT / "deploy" / "workstation" / "sc-digger-gpu-worker.service").read_text(encoding="utf-8")
    exec_line = next(z for z in unit.splitlines() if z.startswith("ExecStart="))
    assert "-m sc_digger.worker" in exec_line
    assert "--idle-exit" in exec_line
    assert "--job-type" not in exec_line


def test_worker_mit_maest_holt_cues_und_embeddings(monkeypatch):
    captured = {}

    def fake_loop(client, job_type=None, poll_interval=5.0, max_jobs=None, handler_map=None,
                  stop_event=None, **kw):
        captured["job_type"] = job_type
        captured["handlers"] = set(handler_map or {})
        return 0

    monkeypatch.setattr(emb, "make_maest_embedder", lambda *a, **k: (lambda path: [1.0, 0.0]))
    monkeypatch.setattr(worker, "run_worker_loop", fake_loop)
    assert worker.main(["--api-url", "http://127.0.0.1:9", "--password", "x", "--idle-exit", "300"]) == 0
    assert captured == {"job_type": None, "handlers": {"cues", "embedding"}}


def test_betrieb_nennt_die_voraussetzung_auf_der_workstation():
    text = (ROOT / "BETRIEB.md").read_text(encoding="utf-8")
    assert '/home/stephanv/allin1_env/bin/python -c "import torch, transformers"' in text
    assert "Embedding-Jobs abgeschaltet" in text
