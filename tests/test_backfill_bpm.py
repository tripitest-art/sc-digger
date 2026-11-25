"""Eigene Tests für den BPM-Backfill (Issue #703)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sc_digger.backfill import backfill_bpm
from sc_digger.db import TrackDB, TrackRecord, TrackStatus


def _row(db: TrackDB, path: Path, **kw):
    return db.upsert_track(TrackRecord(path=str(path), mtime=0.0, size=0, status=TrackStatus.ARCHIVE, **kw))


def _dateien(tmp_path: Path, *namen: str) -> list[Path]:
    out = []
    for n in namen:
        p = tmp_path / n
        p.write_bytes(b"x")
        out.append(p)
    return out


def test_gleicher_wert_zaehlt_als_bestaetigt_und_bekommt_konfidenz(tmp_path):
    (a,) = _dateien(tmp_path, "a.wav")
    with TrackDB(tmp_path / "t.sqlite") as db:
        _row(db, a, bpm=157.0, bpm_source="audio")
        with patch("sc_digger.backfill.analyze_track", return_value={"bpm": 157.02, "bpm_confidence": 0.6}) as m:
            res = backfill_bpm(db, snap_to=(140.0, 170.0))
        rec = db.get_track_by_path(a)
    assert res == {"seen": 1, "set": 0, "would_set": 0, "unchanged": 1, "failed": 0}
    assert rec.bpm_confidence == 0.6
    assert m.call_args.kwargs == {"want_key": False, "snap_to": (140.0, 170.0)}


def test_analysefehler_und_kein_bpm_zaehlen_als_failed_und_lauf_geht_weiter(tmp_path):
    a, b, c = _dateien(tmp_path, "a.wav", "b.wav", "c.wav")
    antworten = iter([RuntimeError("kaputt"), {"bpm": None, "bpm_confidence": 0.0},
                      {"bpm": 155.0, "bpm_confidence": 0.0}])

    def fake(*_a, **_k):
        r = next(antworten)
        if isinstance(r, Exception):
            raise r
        return r

    with TrackDB(tmp_path / "t.sqlite") as db:
        for p in (a, b, c):
            _row(db, p, bpm=161.5, bpm_source="audio")
        with patch("sc_digger.backfill.analyze_track", side_effect=fake):
            res = backfill_bpm(db)
        assert db.get_track_by_path(a).bpm_confidence is None
        assert db.get_track_by_path(b).bpm == 161.5
        rec_c = db.get_track_by_path(c)
    assert res["failed"] == 2 and res["set"] == 1
    assert rec_c.bpm == 155.0 and rec_c.bpm_confidence == 0.0


def test_limit_begrenzt_den_lauf(tmp_path):
    ps = _dateien(tmp_path, "a.wav", "b.wav")
    with TrackDB(tmp_path / "t.sqlite") as db:
        for p in ps:
            _row(db, p, bpm=152.0, bpm_source="audio")
        with patch("sc_digger.backfill.analyze_track", return_value={"bpm": 150.0, "bpm_confidence": 0.5}):
            res = backfill_bpm(db, limit=1)
        assert len(db.tracks_with_legacy_bpm()) == 1
    assert res["seen"] == 1 and res["set"] == 1


def test_set_bpm_unbekannte_id(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        assert db.set_bpm(999, 150.0, 0.5) is False
