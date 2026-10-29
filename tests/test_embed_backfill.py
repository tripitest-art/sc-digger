"""Eigene Tests für embed-backfill (Issue #419): Randfälle neben den Akzeptanztests."""
from pathlib import Path

from sc_digger.db import JobType, TrackDB, TrackRecord, TrackStatus
from sc_digger.embeddings import BackfillSummary, backfill_embedding_jobs


def _track(db: TrackDB, path: Path, **fields) -> int:
    path.write_bytes(b"RIFF")
    rec = db.upsert_track(TrackRecord(path=str(path), mtime=1.0, size=4, status=TrackStatus.INBOX, format="wav"))
    if fields:
        cols = ", ".join(f"{k} = ?" for k in fields)
        db.db.execute(f"UPDATE tracks SET {cols} WHERE id = ?", (*fields.values(), rec.id))
        db.db.commit()
    return rec.id


def test_later_allein_zaehlt_nicht_als_bewertet(tmp_path: Path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        _track(db, tmp_path / "a.wav", feedback="later", sc_id=5)
        db.set_sc_feedback(5, "later")
        assert backfill_embedding_jobs(db) == BackfillSummary()
        assert db.get_pending_jobs(JobType.EMBEDDING) == []


def test_laufender_job_zaehlt_als_already_queued(tmp_path: Path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        tid = _track(db, tmp_path / "a.wav", feedback="like")
        assert backfill_embedding_jobs(db).queued == 1
        claimed = db.claim_next_job(JobType.EMBEDDING)
        assert claimed is not None and claimed.status == "running"
        again = backfill_embedding_jobs(db)
        assert again == BackfillSummary(candidates=1, queued=0, already_queued=1, missing_file=0)
        assert db.find_active_job(JobType.EMBEDDING, tid).id == claimed.id


def test_limit_zaehlt_danach_nichts_mehr(tmp_path: Path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        for n in range(3):
            _track(db, tmp_path / f"{n}.wav", feedback="like")
        s = backfill_embedding_jobs(db, limit=1)
        assert (s.candidates, s.queued, s.already_queued, s.missing_file) == (3, 1, 0, 0)
