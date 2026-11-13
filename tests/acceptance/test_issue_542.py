"""Akzeptanztests: Helligkeit speichern und im Mix-Finder filtern."""

import wave

import numpy as np
from starlette.testclient import TestClient

from sc_digger.backfill import backfill_energy
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.harmonic import find_mix_candidates
from sc_digger.models import Config
from sc_digger.web.app import create_app


def _db(tmp_path):
    db = TrackDB(tmp_path / "tracks.sqlite")
    for name, hz in (("dunkel", 1800.0), ("mitte", 3000.0), ("hell", 3800.0), ("ohne", None)):
        db.upsert_track(TrackRecord(
            path=f"/music/Schranz/{name}.wav", mtime=1.0, size=1,
            artist="A", title=name, key_camelot="8A", bpm=160.0,
            spectral_centroid_hz=hz,
        ))
    return db


def test_ohne_helligkeitsfilter_bleiben_alle_kandidaten(tmp_path):
    # Ohne Angabe darf sich nichts aendern: auch Tracks ohne bekannten Schwerpunkt
    # bleiben in der Auswahl. Laeuft schon heute gruen (Kontrolltest).
    db = TrackDB(tmp_path / "tracks.sqlite")
    for name in ("eins", "zwei"):
        db.upsert_track(TrackRecord(
            path=f"/music/Schranz/{name}.wav", mtime=1.0, size=1,
            artist="A", title=name, key_camelot="8A", bpm=160.0,
        ))
    treffer = find_mix_candidates(db, "8A", 160.0)
    assert {t.title for t in treffer} == {"eins", "zwei"}
    db.db.close()


def test_helligkeitsfilter_grenzt_ein_und_ignoriert_unbekannte(tmp_path):
    db = _db(tmp_path)
    treffer = find_mix_candidates(db, "8A", 160.0, centroid_min=2900.0, centroid_max=3900.0)
    assert {t.title for t in treffer} == {"mitte", "hell"}
    db.db.close()


def test_backfill_speichert_helligkeit(tmp_path):
    db = TrackDB(tmp_path / "tracks.sqlite")
    wav = tmp_path / "track.wav"
    sr, dauer = 22050, 12.0
    t = np.linspace(0, dauer, int(sr * dauer), endpoint=False)
    sig = (0.5 * np.sin(2 * np.pi * 5000.0 * t) * 32767).astype(np.int16)
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(sig.tobytes())
    db.upsert_track(TrackRecord(path=str(wav), mtime=1.0, size=1))

    backfill_energy(db)

    rec = db.list_collection_tracks()[0]
    assert rec.spectral_centroid_hz is not None
    assert rec.spectral_centroid_hz > 4000.0, "5000-Hz-Sinus muss einen hohen Schwerpunkt haben"
    db.db.close()


def test_mix_seite_hat_helligkeitsfelder(tmp_path):
    state_db = tmp_path / "seen.sqlite"
    track_db = tmp_path / "tracks.sqlite"
    TrackDB(track_db).db.close()
    cfg = Config({"state": {"db_path": str(state_db), "track_db_path": str(track_db)}})
    client = TestClient(create_app(cfg, password=None, allow_anonymous=True))
    r = client.get("/mix?key=8A&bpm=160")
    assert r.status_code == 200
    assert 'name="cmin"' in r.text
    assert 'name="cmax"' in r.text
