"""Eigene Tests für die Rückmeldung des Status „übernommen“ (ROADMAP 3.12, Issue #205).

Ergänzt die Akzeptanztests um Matching-Grenzfälle, Dislike-Schutz, Migration,
Sortierung, CLI-Argument und Idempotenz. Netzwerk wird nicht benötigt.
"""
from __future__ import annotations

import random
from pathlib import Path

from sc_digger import audit
from sc_digger.audit import AuditSummary, mark_adopted_tracks, run_audit
from sc_digger.db import MIGRATIONS, TrackDB, TrackRecord, TrackStatus
from sc_digger.fingerprint import encode_fingerprint
from sc_digger import main as main_mod
from sc_digger.models import Config


def rand_fp(seed: int, n: int = 300) -> list[int]:
    rng = random.Random(seed)
    return [rng.getrandbits(32) for _ in range(n)]


FP = rand_fp(1)
FP_OTHER = rand_fp(2)


def _inbox(db: TrackDB, path: str, fp: list[int], duration: float = 300.0,
           feedback: str | None = None) -> TrackRecord:
    rec = db.upsert_track(TrackRecord(
        path=path, mtime=1.0, size=100, status=TrackStatus.INBOX,
    ))
    if feedback is not None:
        db.set_feedback(rec.id, feedback)
    db.set_fingerprint(rec.id, encode_fingerprint(fp), duration)
    return rec


def _archive(db: TrackDB, path: str, fp: list[int], duration: float = 300.0) -> TrackRecord:
    rec = db.upsert_track(TrackRecord(
        path=path, mtime=2.0, size=100, status=TrackStatus.ARCHIVE,
    ))
    db.set_fingerprint(rec.id, encode_fingerprint(fp), duration)
    return rec


def test_migration_8_legt_adopted_at_an(tmp_path):
    """Migration 8 muss die Spalte adopted_at und den Index anlegen."""
    with TrackDB(tmp_path / "t.sqlite") as db:
        cols = {r["name"] for r in db.db.execute("PRAGMA table_info(tracks)")}
        assert "adopted_at" in cols
        row = db.db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_tracks_adopted_at'"
        ).fetchone()
        assert row is not None
        versions = [v for v, _, _ in MIGRATIONS]
        assert 8 in versions


def test_mark_adopted_setzt_adopted_at_und_feedback(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        rec = _inbox(db, "/inbox/a.wav", FP)
        assert db.mark_adopted(rec.id, "2026-01-01 00:00:00") is True
        reloaded = db.get_track_by_id(rec.id)
        assert reloaded.adopted_at == "2026-01-01 00:00:00"
        assert reloaded.feedback == "like"


def test_mark_adopted_ueberschreibt_dislike_nicht(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        rec = _inbox(db, "/inbox/a.wav", FP, feedback="dislike")
        db.mark_adopted(rec.id, "2026-01-01 00:00:00")
        reloaded = db.get_track_by_id(rec.id)
        assert reloaded.feedback == "dislike"
        assert reloaded.adopted_at == "2026-01-01 00:00:00"


def test_mark_adopted_unbekannte_id_gibt_false(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        assert db.mark_adopted(999, "2026-01-01 00:00:00") is False


def test_mark_adopted_tracks_ignoriert_zu_grosse_dauer_differenz(tmp_path):
    """Bei stark abweichender Dauer (hier 30 s) darf kein Treffer entstehen."""
    with TrackDB(tmp_path / "t.sqlite") as db:
        _inbox(db, "/inbox/a.wav", FP, duration=200.0)
        _archive(db, "/music/Schranz/a.wav", FP, duration=230.0)
        assert mark_adopted_tracks(db, now="2026-01-01 00:00:00") == []


def test_mark_adopted_tracks_ignoriert_bereits_uebernommene(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        rec = _inbox(db, "/inbox/a.wav", FP)
        _archive(db, "/music/Schranz/a.wav", FP)
        db.mark_adopted(rec.id, "2026-01-01 00:00:00")
        assert mark_adopted_tracks(db, now="2026-02-02 00:00:00") == []


def test_mark_adopted_tracks_ignoriert_inbox_ohne_fingerprint(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        db.upsert_track(TrackRecord(
            path="/inbox/nofp.wav", mtime=1.0, size=100, status=TrackStatus.INBOX,
        ))
        _archive(db, "/music/Schranz/a.wav", FP)
        assert mark_adopted_tracks(db, now="2026-01-01 00:00:00") == []


def test_get_adopted_tracks_sortierung_desc_dann_id(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        r1 = _inbox(db, "/inbox/a.wav", FP)
        r2 = _inbox(db, "/inbox/b.wav", FP_OTHER)
        db.mark_adopted(r1.id, "2026-01-01 00:00:00")
        db.mark_adopted(r2.id, "2026-02-01 00:00:00")
        got = db.get_adopted_tracks()
        assert [r.path for r in got] == ["/inbox/b.wav", "/inbox/a.wav"]


def test_now_als_datetime_wird_formatiert(tmp_path):
    from datetime import datetime

    with TrackDB(tmp_path / "t.sqlite") as db:
        rec = _inbox(db, "/inbox/a.wav", FP)
        _archive(db, "/music/Schranz/a.wav", FP)
        mark_adopted_tracks(db, now=datetime(2026, 3, 4, 5, 6, 7))
        assert db.get_track_by_id(rec.id).adopted_at == "2026-03-04 05:06:07"


def test_run_audit_ohne_check_adopted_laesst_inbox_unberuehrt(tmp_path, monkeypatch):
    cfg_path = Path(__file__).resolve().parents[1] / "config.yaml"
    cfg = Config.load(cfg_path)
    coll = tmp_path / "Schranz"
    coll.mkdir()
    cfg.raw["download"] = {**cfg["download"], "collection_dir": str(coll)}
    cfg.raw["state"] = {**cfg["state"], "track_db_path": str(tmp_path / "t.sqlite")}

    with TrackDB(cfg["state"]["track_db_path"]) as db:
        rec = _inbox(db, "/inbox/a.wav", FP)
        _archive(db, "/music/Schranz/a.wav", FP)

    monkeypatch.setattr(audit, "audit_collection", lambda *a, **k: AuditSummary())
    monkeypatch.setattr(audit, "fill_fingerprints", lambda *a, **k: (0, 0))

    summary = run_audit(cfg, path=coll)
    assert summary.adopted_tracks == []
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        assert db.get_track_by_id(rec.id).adopted_at is None


def test_cli_audit_hat_check_adopted_flag(monkeypatch):
    """`audit --check-adopted` muss den Schalter bis zu run_audit durchreichen."""
    captured: dict = {}

    def fake_run_audit(cfg, **kwargs):
        captured.update(kwargs)
        return AuditSummary()

    monkeypatch.setattr(main_mod, "run_audit", fake_run_audit)
    monkeypatch.setattr(main_mod, "install_redacting_logging", lambda *a, **k: None)
    monkeypatch.setattr(Config, "load", classmethod(lambda cls, *a, **k: cls({})))

    monkeypatch.setattr("sys.argv", ["sc-digger", "audit", "--check-adopted"])
    main_mod.cli()
    assert captured.get("check_adopted") is True
