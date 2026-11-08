"""Akzeptanztests: Energie wird beim Analysieren bestimmt und angezeigt."""

import sqlite3
import wave

import numpy as np
from starlette.testclient import TestClient

from sc_digger.analysis import analyze_track
from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.web.app import create_app


def _synth(path, freq: float, duration: float = 12.0, sr: int = 22050):
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    sig = (0.5 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(sig.tobytes())
    return path


def test_analyze_track_liefert_energie_stufe(tmp_path):
    dunkel = _synth(tmp_path / "dunkel.wav", freq=500.0)
    hell = _synth(tmp_path / "hell.wav", freq=6000.0)

    a = analyze_track(dunkel, want_bpm=False, want_key=False)
    b = analyze_track(hell, want_bpm=False, want_key=False)

    assert isinstance(a["energy"], int)
    assert isinstance(b["energy"], int)
    assert 1 <= a["energy"] <= 5
    assert 1 <= b["energy"] <= 5
    assert b["energy"] > a["energy"]


def test_tracks_seite_zeigt_energie_spalte(tmp_path):
    state_db = tmp_path / "seen.sqlite"
    track_db = tmp_path / "tracks.sqlite"
    with sqlite3.connect(state_db) as conn:
        conn.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
    TrackDB(track_db).db.close()
    cfg = Config({"state": {"db_path": str(state_db), "track_db_path": str(track_db)}})
    client = TestClient(create_app(cfg, password=None, allow_anonymous=True))
    r = client.get("/tracks")
    assert r.status_code == 200
    assert "Energie" in r.text
