"""Eigene Tests für Fingerprints in TrackDB und Duplikaterkennung (Issue #38)."""
from __future__ import annotations

import random
from pathlib import Path

import pytest

from sc_digger import fingerprint as fpm
from sc_digger.db import QualityStatus, TrackDB, TrackRecord
from sc_digger.fingerprint import (
    Fingerprint,
    decode_fingerprint,
    duplicate_groups,
    encode_fingerprint,
    find_same_recording,
)


def rand_fp(seed: int, n: int = 300) -> list[int]:
    rng = random.Random(seed)
    return [rng.getrandbits(32) for _ in range(n)]


@pytest.fixture
def db(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as d:
        yield d


def test_set_fingerprint_updates_updated_at_but_not_quality_or_bpm(db):
    """set_fingerprint ändert updated_at, lässt aber quality_status und bpm unverändert."""
    r = db.upsert_track(
        TrackRecord(
            path="/music/test.wav",
            mtime=100.0,
            size=2000,
            quality_status=QualityStatus.OK,
            bpm=155.0,
        )
    )
    # updated_at manuell auf einen alten Zeitstempel setzen
    old_ts = "2020-01-01 00:00:00"
    db.db.execute("UPDATE tracks SET updated_at = ? WHERE id = ?", (old_ts, r.id))
    db.db.commit()

    r_old = db.get_track_by_id(r.id)
    assert r_old.updated_at == old_ts
    assert r_old.quality_status == QualityStatus.OK.value
    assert r_old.bpm == 155.0

    # Fingerprint setzen
    ok = db.set_fingerprint(r.id, "my_encoded_fp", 300.5)
    assert ok is True

    r_new = db.get_track_by_id(r.id)
    assert r_new.fingerprint == "my_encoded_fp"
    assert r_new.fingerprint_duration == 300.5
    # updated_at wurde aktualisiert
    assert r_new.updated_at != old_ts
    # quality_status und bpm blieben unverändert
    assert r_new.quality_status == QualityStatus.OK.value
    assert r_new.bpm == 155.0
    assert r_new.mtime == 100.0
    assert r_new.size == 2000


def test_duplicate_groups_empty():
    """duplicate_groups([]) ist []."""
    assert duplicate_groups([]) == []

    # Auch mit Records ohne Fingerprint
    recs_empty = [
        TrackRecord(path="/m/1.wav", mtime=1.0, size=1),
        TrackRecord(path="/m/2.wav", mtime=1.0, size=1),
    ]
    assert duplicate_groups(recs_empty) == []


def test_duplicate_groups_transitive_clustering(monkeypatch):
    """Drei Dateien, die nur über ein mittleres Glied verbunden sind (A~B, B~C, A≁C), werden eine Gruppe."""
    # Definiere Beziehungen über monkeypatch von same_recording
    # A ~ B (True), B ~ C (True), A ~ C (False)
    def fake_same(fp1, fp2, **k):
        v1, v2 = fp1.values[0], fp2.values[0]
        pair = {v1, v2}
        if pair == {1, 2}:  # A ~ B
            return True
        if pair == {2, 3}:  # B ~ C
            return True
        return False  # A ~ C ist False

    monkeypatch.setattr(fpm, "same_recording", fake_same)

    recs = [
        TrackRecord(path="/m/c_track.wav", mtime=1.0, size=1, id=3, fingerprint=encode_fingerprint([3] * 100), fingerprint_duration=301.0),
        TrackRecord(path="/m/a_track.wav", mtime=1.0, size=1, id=1, fingerprint=encode_fingerprint([1] * 100), fingerprint_duration=300.0),
        TrackRecord(path="/m/b_track.wav", mtime=1.0, size=1, id=2, fingerprint=encode_fingerprint([2] * 100), fingerprint_duration=300.5),
    ]

    groups = duplicate_groups(recs)
    assert len(groups) == 1
    # Gruppe enthält alle 3 Records, nach Pfad sortiert
    group = groups[0]
    assert [r.id for r in group] == [1, 2, 3]
    assert [r.path for r in group] == ["/m/a_track.wav", "/m/b_track.wav", "/m/c_track.wav"]


def test_fingerprint_candidates_boundaries(db):
    """Prüft genaue Grenzen der Dauer-Toleranz in fingerprint_candidates."""
    # Basis: 200.0s, Toleranz 2.0s -> Kandidaten von 198.0 bis 202.0
    _rec_under = db.upsert_track(TrackRecord(path="/m/under.wav", mtime=1.0, size=1))
    db.set_fingerprint(_rec_under.id, "fp", 197.9)

    _rec_min = db.upsert_track(TrackRecord(path="/m/min.wav", mtime=1.0, size=1))
    db.set_fingerprint(_rec_min.id, "fp", 198.0)

    _rec_exact = db.upsert_track(TrackRecord(path="/m/exact.wav", mtime=1.0, size=1))
    db.set_fingerprint(_rec_exact.id, "fp", 200.0)

    _rec_max = db.upsert_track(TrackRecord(path="/m/max.wav", mtime=1.0, size=1))
    db.set_fingerprint(_rec_max.id, "fp", 202.0)

    _rec_over = db.upsert_track(TrackRecord(path="/m/over.wav", mtime=1.0, size=1))
    db.set_fingerprint(_rec_over.id, "fp", 202.1)

    candidates = db.fingerprint_candidates(200.0, 2.0)
    assert [c.id for c in candidates] == [_rec_min.id, _rec_exact.id, _rec_max.id]
