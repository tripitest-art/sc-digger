"""Akzeptanztests: Embedding-Jobs für vorhandene Tracks nachträglich einreihen (embed-backfill)."""
from pathlib import Path

from sc_digger import main as m
from sc_digger.db import JobType, TrackDB, TrackRecord, TrackStatus
from sc_digger.embeddings import BackfillSummary, backfill_embedding_jobs
from sc_digger.models import Config

ROOT = Path(__file__).resolve().parents[2]


def _track(db: TrackDB, path: Path, *, create: bool = True, status=TrackStatus.INBOX, **fields) -> int:
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"RIFF")
    rec = db.upsert_track(TrackRecord(path=str(path), mtime=1.0, size=4, status=status, format="wav"))
    if fields:
        cols = ", ".join(f"{k} = ?" for k in fields)
        db.db.execute(f"UPDATE tracks SET {cols} WHERE id = ?", (*fields.values(), rec.id))
        db.db.commit()
    return rec.id


def _queued_ids(db: TrackDB) -> list[int]:
    return sorted(j.track_id for j in db.get_pending_jobs(JobType.EMBEDDING))


def _fixture(tmp_path: Path, db: TrackDB) -> dict[str, int]:
    ids = {
        "like": _track(db, tmp_path / "a.wav", feedback="like"),
        "dislike": _track(db, tmp_path / "b.wav", feedback="dislike"),
        "rating": _track(db, tmp_path / "c.wav", status=TrackStatus.ARCHIVE, rb_rating=3),
        "digest_like": _track(db, tmp_path / "d.wav", sc_id=111),
        "ohne_label": _track(db, tmp_path / "e.wav"),
        "later": _track(db, tmp_path / "f.wav", feedback="later"),
        "fehlt": _track(db, tmp_path / "weg.wav", create=False, feedback="like"),
        "hat_embedding": _track(db, tmp_path / "g.wav", feedback="like"),
        "uebernommen_inbox": _track(db, tmp_path / "h.wav", create=False, sc_id=222,
                                    adopted_at="2026-10-01 10:00:00"),
        "archiv_zwilling": _track(db, tmp_path / "coll" / "h.wav", status=TrackStatus.ARCHIVE, sc_id=222),
    }
    db.set_sc_feedback(111, "like")
    db.set_embedding(ids["hat_embedding"], "test-modell", [0.1, 0.2])
    return ids


def test_nur_bewertete_tracks_mit_datei_werden_eingereiht(tmp_path: Path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        ids = _fixture(tmp_path, db)
        summary = backfill_embedding_jobs(db)
        expected = sorted(ids[k] for k in ("like", "dislike", "rating", "digest_like", "archiv_zwilling"))
        assert _queued_ids(db) == expected
        assert summary == BackfillSummary(candidates=7, queued=5, already_queued=0, missing_file=2)
        job = db.find_active_job(JobType.EMBEDDING, ids["like"])
        assert job.payload == {"track_id": ids["like"], "path": str(tmp_path / "a.wav"), "format": "wav"}


def test_zweiter_lauf_legt_keine_duplikate_an(tmp_path: Path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        _fixture(tmp_path, db)
        backfill_embedding_jobs(db)
        before = _queued_ids(db)
        again = backfill_embedding_jobs(db)
        assert _queued_ids(db) == before
        assert again.queued == 0
        assert again.already_queued == 5


def test_all_nimmt_unbewertete_dazu_bewertete_zuerst(tmp_path: Path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        ids = _fixture(tmp_path, db)
        first = backfill_embedding_jobs(db, labeled_only=False, limit=5)
        assert first.queued == 5
        assert _queued_ids(db) == sorted(
            ids[k] for k in ("like", "dislike", "rating", "digest_like", "archiv_zwilling"))
        rest = backfill_embedding_jobs(db, labeled_only=False)
        assert rest.queued == 2
        assert ids["ohne_label"] in _queued_ids(db) and ids["later"] in _queued_ids(db)
        assert ids["hat_embedding"] not in _queued_ids(db)


def test_limit_und_dry_run(tmp_path: Path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        ids = _fixture(tmp_path, db)
        dry = backfill_embedding_jobs(db, dry_run=True)
        assert dry.queued == 5
        assert _queued_ids(db) == []
        limited = backfill_embedding_jobs(db, limit=2)
        assert limited.queued == 2
        assert _queued_ids(db) == sorted([ids["like"], ids["dislike"]])


def test_cli_embed_backfill(tmp_path: Path, capsys):
    cfg = Config.load(ROOT / "config.yaml")
    track_db = tmp_path / "tracks.sqlite"
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"), "track_db_path": str(track_db)}
    with TrackDB(track_db) as db:
        ids = _fixture(tmp_path, db)

    text = m.run_embed_backfill(cfg, dry_run=True)
    assert text == "Embedding-Backfill: 5 würden eingereiht, 0 schon in der Queue, 2 ohne Datei (7 Kandidaten)"
    assert text in capsys.readouterr().out

    text = m.run_embed_backfill(cfg, labeled_only=False, limit=1)
    assert text == "Embedding-Backfill: 1 eingereiht, 0 schon in der Queue, 0 ohne Datei (9 Kandidaten)"
    with TrackDB(track_db) as db:
        assert _queued_ids(db) == [ids["like"]]
