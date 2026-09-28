"""Akzeptanztests Issue #16: Track-DB-Nacharbeiten (Pfade, hängende Jobs, kein Default-Pfad)."""
import inspect

import pytest

from sc_digger.db import STALE_TIMEOUT_MINUTES, JobType, TrackDB, TrackRecord, _normalize_path


@pytest.fixture
def db(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as d:
        yield d


def _age(db, job_id: int, minutes: int) -> None:
    db.db.execute("UPDATE jobs SET updated_at = datetime('now', ?) WHERE id = ?", (f"-{minutes} minutes", job_id))
    db.db.commit()


def _running(db, *job_types):
    t = db.upsert_track(TrackRecord(path="/music/inbox/x.wav", mtime=1.0, size=1))
    jobs = [db.enqueue_job(t.id, jt) for jt in job_types]
    for _ in jobs:
        db.claim_next_job()
    return jobs


# ---------------- Pfade ----------------
@pytest.mark.parametrize("spelling", [
    "/music/Schranz/a/../x.wav",
    "/music//Schranz/./x.wav",
    "/music/Schranz/x.wav/",
    "\\music\\Schranz\\x.wav",
])
def test_equivalent_spellings_normalize_to_one(spelling):
    assert _normalize_path(spelling) == "/music/Schranz/x.wav"


def test_relative_paths_are_not_made_absolute():
    assert _normalize_path("inbox/sub/../x.wav") == "inbox/x.wav"


def test_two_spellings_give_one_row(db):
    db.upsert_track(TrackRecord(path="/music/Schranz/a/../x.wav", mtime=1.0, size=1))
    db.upsert_track(TrackRecord(path="/music/Schranz/x.wav", mtime=2.0, size=1))
    rows = db.db.execute("SELECT path, mtime FROM tracks").fetchall()
    assert [(r["path"], r["mtime"]) for r in rows] == [("/music/Schranz/x.wav", 2.0)]
    assert db.get_track_by_path("/music//Schranz/./x.wav").mtime == 2.0


# ---------------- Hängende Jobs ----------------
def test_default_timeouts_per_job_type():
    assert STALE_TIMEOUT_MINUTES == {"embedding": 120, "caption": 30, "tag_backfill": 30, "fingerprint": 30}


def test_requeue_without_argument_uses_per_type_timeout(db):
    emb, cap = _running(db, JobType.EMBEDDING, JobType.CAPTION)
    _age(db, emb.id, 60)
    _age(db, cap.id, 60)
    assert db.requeue_stale_jobs() == 1
    assert db.get_job_by_id(emb.id).status == "running"   # 60 < 120: legitim lange
    assert db.get_job_by_id(cap.id).status == "pending"   # 60 > 30: hängt


def test_explicit_timeout_applies_to_all_types(db):
    (emb,) = _running(db, JobType.EMBEDDING)
    _age(db, emb.id, 60)
    assert db.requeue_stale_jobs(timeout_minutes=30) == 1
    assert db.get_job_by_id(emb.id).status == "pending"


def test_touch_job_keeps_long_running_job_alive(db):
    (emb,) = _running(db, JobType.EMBEDDING)
    _age(db, emb.id, 500)
    assert db.touch_job(emb.id) is True
    assert db.requeue_stale_jobs() == 0
    assert db.get_job_by_id(emb.id).status == "running"


def test_touch_job_only_for_running_jobs(db):
    t = db.upsert_track(TrackRecord(path="/music/inbox/y.wav", mtime=1.0, size=1))
    pending = db.enqueue_job(t.id, JobType.CAPTION)
    assert db.touch_job(pending.id) is False
    assert db.touch_job(999_999) is False


# ---------------- Kein fester Default-Pfad ----------------
def test_trackdb_path_has_no_default():
    assert inspect.signature(TrackDB.__init__).parameters["db_path"].default is inspect.Parameter.empty
