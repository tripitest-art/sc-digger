"""Akzeptanztests: Cue- und Strukturanalyse-Jobs in der Job-Queue (Issue #227 Teil 1)."""
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from sc_digger import cues
from sc_digger.cues import apply_cue_result
from sc_digger.db import JobStatus, JobType, QualityStatus, TrackDB, TrackRecord, TrackStatus, enqueue_cue_job
from sc_digger.models import Config
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "test-cue-jobs-pwd"
AUTH = ("sc", PASSWORD)


def _make_cfg(tmp_path: Path, inbox_dir: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    cfg.raw["download"] = {
        "inbox_dir": str(inbox_dir),
        "collection_dir": str(tmp_path / "collection"),
    }
    cfg.raw["cues"] = {"auto_queue": True}
    return cfg


def test_job_type_cues_and_enqueue_cue_job(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        rec = db.upsert_track(TrackRecord(path="inbox/track1.mp3", mtime=1.0, size=100, status=TrackStatus.INBOX))
        job = enqueue_cue_job(db, rec)

        assert job is not None
        assert job.job_type == JobType.CUES
        assert job.status == JobStatus.PENDING
        assert job.track_id == rec.id
        assert job.payload["path"] == "inbox/track1.mp3"


def test_get_job_audio_serves_file_and_protects_missing(tmp_path: Path):
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    audio_file = inbox / "test_track.mp3"
    audio_file.write_bytes(b"ID3" + b"\x00" * 200)

    cfg = _make_cfg(tmp_path, inbox)
    client = TestClient(create_app(cfg, password=PASSWORD))

    with TrackDB(cfg["state"]["track_db_path"]) as db:
        rec = db.upsert_track(TrackRecord(path=str(audio_file), mtime=1.0, size=203, status=TrackStatus.INBOX))
        job = enqueue_cue_job(db, rec)
        job_id = job.id

    # 1. Erfolgreicher Download
    resp = client.get(f"/api/jobs/{job_id}/audio", auth=AUTH)
    assert resp.status_code == 200
    assert resp.content == b"ID3" + b"\x00" * 200

    # 2. 404 bei unbekanntem Job
    resp_missing = client.get("/api/jobs/99999/audio", auth=AUTH)
    assert resp_missing.status_code == 404


def test_complete_cue_job_updates_track_db_and_tags_inbox_file(tmp_path: Path):
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    audio_file = inbox / "banger.mp3"
    audio_file.write_bytes(b"\x00" * 100)

    cfg = _make_cfg(tmp_path, inbox)
    client = TestClient(create_app(cfg, password=PASSWORD))

    with TrackDB(cfg["state"]["track_db_path"]) as db:
        rec = db.upsert_track(TrackRecord(path=str(audio_file), mtime=1.0, size=100, status=TrackStatus.INBOX))
        job = enqueue_cue_job(db, rec)
        job_id = job.id

    # Worker meldet Job als completed
    result_data = {
        "first_kick_s": 8.0,
        "drop_s": 32.5,
        "drop2_s": 96.0,
        "breakdown_s": 64.0,
        "breakdown_kind": "groove",
        "breakdown_ratio": 0.18,
        "cues": [{"name": "First Kick", "time": 8.0}, {"name": "Drop", "time": 32.5}],
    }
    resp = client.post(
        f"/api/jobs/{job_id}/complete",
        json={"status": "completed", "result": result_data},
        auth=AUTH,
    )
    assert resp.status_code == 200

    # Prüfe Track-DB
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        updated = db.get_track_by_id(rec.id)
        assert updated is not None
        assert updated.breakdown_ratio == 0.18
        assert "First Kick" in str(updated.rb_cues)

    # Prüfe Audio-Tags in der Inbox-Datei
    assert cues.read_first_kick_tag(audio_file) == 8.0
    assert cues.read_drop_tag(audio_file) == 32.5


def test_apply_cue_result_never_modifies_archive_files(tmp_path: Path):
    inbox = tmp_path / "inbox"
    archive = tmp_path / "collection"
    inbox.mkdir(parents=True, exist_ok=True)
    archive.mkdir(parents=True, exist_ok=True)

    archive_file = archive / "sacred_archive.mp3"
    archive_file.write_bytes(b"\x00" * 100)
    orig_bytes = archive_file.read_bytes()

    cfg = _make_cfg(tmp_path, inbox)
    db_path = cfg["state"]["track_db_path"]

    with TrackDB(db_path) as db:
        rec = db.upsert_track(TrackRecord(path=str(archive_file), mtime=1.0, size=100, status=TrackStatus.ARCHIVE))
        job = enqueue_cue_job(db, rec)

        ok = apply_cue_result(
            db,
            job.id,
            {"first_kick_s": 12.0, "drop_s": 48.0, "cues": [{"name": "Drop", "time": 48.0}]},
            inbox_dir=inbox,
        )
        assert ok is True

        # DB muss aktualisiert sein
        updated = db.get_track_by_id(rec.id)
        assert "Drop" in str(updated.rb_cues)

    # Archiv-Datei darf kein einziges Byte geändert haben (Goldene Regel 4)
    assert archive_file.read_bytes() == orig_bytes
    assert cues.read_first_kick_tag(archive_file) is None
