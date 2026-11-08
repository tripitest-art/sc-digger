"""Akzeptanztests: Energie-Backfill fuer bestehende Tracks."""

import wave

import numpy as np

from sc_digger.backfill import backfill_energy
from sc_digger.db import TrackDB, TrackRecord


def _synth(path, freq: float, duration: float = 12.0, sr: int = 22050):
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    sig = (0.5 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(sig.tobytes())
    return path


def _db_mit_track(tmp_path, freq=6000.0):
    db = TrackDB(tmp_path / "tracks.sqlite")
    wav = _synth(tmp_path / "track.wav", freq)
    rec = db.upsert_track(TrackRecord(path=str(wav), mtime=1.0, size=1))
    return db, rec


def test_backfill_setzt_stufe_fuer_offene_tracks(tmp_path):
    db, rec = _db_mit_track(tmp_path)

    res = backfill_energy(db)

    assert res["seen"] == 1
    assert res["set"] == 1
    assert res["would_set"] == 0
    assert 1 <= db.get_track_by_id(rec.id).energy <= 5
    db.db.close()


def test_vorhandene_stufe_bleibt_unveraendert(tmp_path):
    db, rec = _db_mit_track(tmp_path)
    assert db.set_energy(rec.id, 2)

    res = backfill_energy(db)

    assert res["skipped"] == 1
    assert res["set"] == 0
    assert db.get_track_by_id(rec.id).energy == 2
    db.db.close()


def test_dry_run_schreibt_nichts(tmp_path):
    db, rec = _db_mit_track(tmp_path)

    res = backfill_energy(db, dry_run=True)

    assert res["would_set"] == 1
    assert res["set"] == 0
    assert db.get_track_by_id(rec.id).energy is None
    db.db.close()


def test_fehlende_datei_bricht_den_lauf_nicht_ab(tmp_path):
    db = TrackDB(tmp_path / "tracks.sqlite")
    db.upsert_track(TrackRecord(path=str(tmp_path / "weg.wav"), mtime=1.0, size=1))

    res = backfill_energy(db)

    assert res["failed"] == 1
    assert res["set"] == 0
    db.db.close()
