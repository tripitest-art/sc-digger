"""Akzeptanztests: Fingerprints der Sammlung in der Track-DB und Report doppelter Aufnahmen (nur lesen)."""
import random
from pathlib import Path

import pytest

from sc_digger import audit
from sc_digger import fingerprint as fpm
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.fingerprint import (MAX_DURATION_DIFF_S, Fingerprint, decode_fingerprint, duplicate_groups,
                                   encode_fingerprint, find_same_recording)
from sc_digger.models import Config


def rand_fp(seed: int, n: int = 300) -> list[int]:
    rng = random.Random(seed)
    return [rng.getrandbits(32) for _ in range(n)]


def flip(values: list[int], p: float, seed: int) -> list[int]:
    """Kippt jedes Bit mit Wahrscheinlichkeit p (simuliert ein anderes Encoding)."""
    rng = random.Random(seed)
    return [v ^ sum(1 << b for b in range(32) if rng.random() < p) for v in values]


A, B, C = rand_fp(1), rand_fp(2), rand_fp(3)


@pytest.fixture
def db(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as d:
        yield d


def _rec(db, path, fp=None, dur=None) -> TrackRecord:
    r = db.upsert_track(TrackRecord(path=str(path), mtime=1.0, size=1))
    if fp is not None:
        assert db.set_fingerprint(r.id, encode_fingerprint(fp), dur) is True
    return db.get_track_by_id(r.id)


# ---------------- TrackDB ----------------
def test_set_fingerprint_keeps_other_fields(db):
    r = _rec(db, "/music/Schranz/a.wav", A, 300.5)
    assert decode_fingerprint(r.fingerprint) == A and r.fingerprint_duration == 300.5
    assert (r.mtime, r.size) == (1.0, 1)                  # Audit-Stand bleibt gültig
    assert db.set_fingerprint(9999, "x", 1.0) is False    # unbekannte ID


def test_tracks_missing_fingerprint(db):
    a = _rec(db, "/m/a.wav")
    _rec(db, "/m/b.wav", A, 300.0)
    c = _rec(db, "/m/c.wav")
    assert [r.id for r in db.tracks_missing_fingerprint()] == [a.id, c.id]


def test_fingerprint_candidates_filter_by_duration(db):
    a = _rec(db, "/m/a.wav", A, 300.0)
    b = _rec(db, "/m/b.wav", B, 303.0)      # Grenze eingeschlossen
    _rec(db, "/m/c.wav", C, 303.5)
    _rec(db, "/m/d.wav")                     # ohne Fingerprint
    e = _rec(db, "/m/e.wav", C, 297.0)
    assert [r.id for r in db.fingerprint_candidates(300.0, 3.0)] == [a.id, b.id, e.id]


def test_tracks_with_fingerprint_sorted_by_duration(db):
    a = _rec(db, "/m/a.wav", A, 300.0)
    _rec(db, "/m/b.wav")
    c = _rec(db, "/m/c.wav", C, 120.0)
    d = _rec(db, "/m/d.wav", B, 300.0)
    assert [r.id for r in db.tracks_with_fingerprint()] == [c.id, a.id, d.id]


# ---------------- Gleiche Aufnahme in der DB finden ----------------
def test_find_same_recording(db):
    _rec(db, "/m/other.wav", B, 300.0)
    orig = _rec(db, "/m/orig.wav", A, 300.0)
    _rec(db, "/m/copy.flac", flip(A, 0.02, seed=5), 300.2)
    assert find_same_recording(Fingerprint(flip(A, 0.02, seed=9), 301.0), db).id == orig.id  # erster Treffer (ID)
    assert find_same_recording(Fingerprint(A, 250.0), db) is None      # Radio-Edit: andere Dauer
    assert find_same_recording(Fingerprint(C, 300.0), db) is None      # anderer Track


def test_find_same_recording_asks_db_for_candidates_only():
    calls = []

    class FakeDB:
        def fingerprint_candidates(self, duration, tolerance_s):
            calls.append((duration, tolerance_s))
            return []

    assert find_same_recording(Fingerprint(A, 301.0), FakeDB()) is None
    assert calls == [(301.0, MAX_DURATION_DIFF_S)]


# ---------------- Gruppen doppelter Aufnahmen ----------------
def _r(i: int, path: str, fp, dur) -> TrackRecord:
    return TrackRecord(path=path, mtime=1.0, size=1, id=i,
                       fingerprint=encode_fingerprint(fp) if fp is not None else None, fingerprint_duration=dur)


def test_duplicate_groups():
    recs = [
        _r(1, "/m/x/Track A.wav", A, 300.0),
        _r(2, "/m/a/Track A (copy).flac", flip(A, 0.02, seed=2), 301.0),
        _r(3, "/m/b.wav", B, 300.0),
        _r(4, "/m/Track A Radio Edit.wav", A, 250.0),
        _r(5, "/m/c1.wav", C, 400.0),
        _r(6, "/m/c2.mp3", flip(C, 0.03, seed=6), 400.5),
        _r(7, "/m/z/Track A.mp3", flip(A, 0.03, seed=7), 302.0),
        _r(8, "/m/ohne.wav", None, None),
    ]
    groups = duplicate_groups(recs)
    assert [[r.id for r in g] for g in groups] == [[2, 1, 7], [5, 6]]   # je Gruppe nach Pfad, Gruppen nach erstem Pfad


def test_duplicate_groups_compares_only_similar_durations(monkeypatch):
    calls = []
    monkeypatch.setattr(fpm, "same_recording", lambda a, b, **k: calls.append(1) or True)
    recs = [_r(i, f"/m/{i}.wav", A, 100.0 + 10 * i) for i in range(50)]
    assert duplicate_groups(recs) == []
    assert calls == []          # kein Paar liegt innerhalb von 3 s: kein einziger Vergleich


# ---------------- Audit füllt Fingerprints ----------------
def test_fill_fingerprints(db, tmp_path):
    ok = tmp_path / "ok.wav"
    bad = tmp_path / "bad.wav"
    boom = tmp_path / "boom.wav"
    for p in (ok, bad, boom):
        p.write_bytes(b"x")
    _rec(db, ok)
    _rec(db, bad)
    _rec(db, boom)
    _rec(db, tmp_path / "geloescht.wav")          # Datei existiert nicht mehr
    _rec(db, "/m/done.wav", B, 200.0)             # hat schon einen
    calls = []

    def compute(p):
        calls.append(Path(p).name)
        if Path(p).name == "boom.wav":
            raise RuntimeError("kaputt")
        return Fingerprint(A, 300.0) if Path(p).name == "ok.wav" else None

    assert audit.fill_fingerprints(db, compute=compute) == (1, 2)
    assert calls == ["ok.wav", "bad.wav", "boom.wav"]
    r = db.get_track_by_path(ok)
    assert decode_fingerprint(r.fingerprint) == A and r.fingerprint_duration == 300.0
    calls.clear()
    assert audit.fill_fingerprints(db, compute=compute) == (0, 2)   # nur die offenen erneut
    assert calls == ["bad.wav", "boom.wav"]


def test_run_audit_fills_fingerprints_and_reports_duplicates(tmp_path, monkeypatch, capsys):
    cfg = Config.load(Path(__file__).resolve().parents[2] / "config.yaml")
    coll = tmp_path / "Schranz"
    coll.mkdir()
    cfg.raw["download"] = {**cfg["download"], "collection_dir": str(coll)}
    cfg.raw["state"] = {**cfg["state"], "track_db_path": str(tmp_path / "tracks.sqlite")}
    new = coll / "neu.wav"
    new.write_bytes(b"x")
    with TrackDB(cfg["state"]["track_db_path"]) as d:
        _rec(d, coll / "Artist - Tune.wav", C, 400.0)
        _rec(d, coll / "Artist - Tune (1).flac", flip(C, 0.02, seed=4), 400.4)
        _rec(d, new)
    monkeypatch.setattr(audit, "audit_collection", lambda *a, **k: audit.AuditSummary())
    monkeypatch.setattr(fpm, "compute_fingerprint", lambda p, **k: Fingerprint(A, 300.0))

    s = audit.run_audit(cfg, path=coll)
    assert (s.fingerprints_new, s.fingerprints_failed) == (1, 0)
    expected = sorted([str(coll / "Artist - Tune (1).flac").replace("\\", "/"),
                       str(coll / "Artist - Tune.wav").replace("\\", "/")])
    assert s.duplicate_groups == [expected]
    out = capsys.readouterr().out
    assert "DOPPELTE AUFNAHMEN" in out and "Artist - Tune (1).flac" in out
    assert sorted(p.name for p in coll.iterdir()) == ["neu.wav"]   # Sammlung unverändert
