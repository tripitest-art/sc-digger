"""Akzeptanztests: erzwungener Backfill zieht fehlende Einzelwerte nach."""

import wave

import numpy as np

from sc_digger.backfill import backfill_energy
from sc_digger.db import TrackDB, TrackRecord


def _synth(path, freq: float = 5000.0, duration: float = 12.0, sr: int = 22050):
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    sig = (0.5 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(sig.tobytes())
    return path


def _db_mit_track_ohne_centroid(tmp_path):
    db = TrackDB(tmp_path / "tracks.sqlite")
    wav = _synth(tmp_path / "track.wav")
    rec = db.upsert_track(TrackRecord(path=str(wav), mtime=1.0, size=1, energy=3))
    return db, rec


def test_ohne_force_wird_nichts_nachgezogen(tmp_path):
    db, rec = _db_mit_track_ohne_centroid(tmp_path)
    res = backfill_energy(db)
    assert res["skipped"] == 1
    assert db.get_track_by_id(rec.id).spectral_centroid_hz is None
    db.db.close()


def test_force_schreibt_den_centroid(tmp_path):
    db, rec = _db_mit_track_ohne_centroid(tmp_path)
    res = backfill_energy(db, force=True)
    assert res["set"] == 1
    aktualisiert = db.get_track_by_id(rec.id)
    assert aktualisiert.spectral_centroid_hz is not None
    assert aktualisiert.spectral_centroid_hz > 4000.0
    assert aktualisiert.energy is not None
    db.db.close()


def test_tracks_ohne_centroid_listet_nur_diese(tmp_path):
    db = TrackDB(tmp_path / "tracks.sqlite")
    mit = db.upsert_track(TrackRecord(path="/music/a.wav", mtime=1.0, size=1,
                                      energy=2, spectral_centroid_hz=3000.0))
    ohne = db.upsert_track(TrackRecord(path="/music/b.wav", mtime=1.0, size=1, energy=2))
    ids = [t.id for t in db.tracks_without_centroid()]
    assert ids == [ohne.id]
    assert mit.id not in ids
    db.db.close()
