"""Akzeptanztests: alte BPM-Rasterwerte der Sammlung erkennen und neu messen (Issue <N>)."""
from __future__ import annotations

import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from sc_digger.audit import audit_file
from sc_digger.backfill import backfill_bpm
from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from sc_digger.web.tasks import action_labels, default_runners

ROOT = Path(__file__).resolve().parents[2]


def _kick_wav(path: Path, bpm: float, seconds: float = 40.0, sr: int = 22050) -> Path:
    """Gleichmäßige Kick-Spur mit Offbeat-Hi-Hat, deterministisch."""
    y = np.zeros(int(seconds * sr))
    t = np.arange(int(0.12 * sr)) / sr
    kick = np.sin(2 * np.pi * (50 + 120 * np.exp(-t * 30)) * t) * np.exp(-t * 25)
    hat_len = int(0.02 * sr)
    hat = np.random.default_rng(0).standard_normal(hat_len) * np.exp(-np.arange(hat_len) / sr * 200) * 0.3
    step = 60.0 / bpm
    n = 0
    while True:
        s = int(round(n * step * sr))
        h = int(round((n + 0.5) * step * sr))
        if h + hat_len >= y.size or s + kick.size >= y.size:
            break
        y[s:s + kick.size] += kick
        y[h:h + hat_len] += hat
        n += 1
    pcm = (y / np.max(np.abs(y)) * 0.8 * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


def _row(db: TrackDB, path: Path, **kw):
    return db.upsert_track(TrackRecord(path=str(path), mtime=0.0, size=0, status=TrackStatus.ARCHIVE, **kw))


def test_altwerte_werden_erkannt(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        _row(db, tmp_path / "alt.wav", bpm=161.5, bpm_source="audio")
        _row(db, tmp_path / "neu.wav", bpm=157.0, bpm_source="audio", bpm_confidence=0.7)
        _row(db, tmp_path / "tag.wav", bpm=150.0, bpm_source="tag")
        _row(db, tmp_path / "ohne.wav")
        pfade = [Path(r.path).name for r in db.tracks_with_legacy_bpm()]
    assert pfade == ["alt.wav"]


def test_backfill_misst_den_altwert_neu(tmp_path):
    audio = _kick_wav(tmp_path / "Herr Blaubeer - Evil Society.wav", 157.0)
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        _row(db, audio, bpm=161.5, bpm_source="audio")
        res = backfill_bpm(db)
        rec = db.get_track_by_path(audio)
        assert res["seen"] == 1 and res["set"] == 1 and res["failed"] == 0
        assert rec.bpm == pytest.approx(157.0, abs=0.5)
        assert rec.bpm_confidence is not None
        assert rec.bpm_source == "audio"
        assert db.tracks_with_legacy_bpm() == []
        assert backfill_bpm(db)["seen"] == 0


def test_fehlende_datei_bleibt_unveraendert(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        _row(db, tmp_path / "fehlt.wav", bpm=152.0, bpm_source="audio")
        res = backfill_bpm(db)
        rec = db.get_track_by_path(tmp_path / "fehlt.wav")
    assert res["failed"] == 1 and res["set"] == 0
    assert rec.bpm == 152.0
    assert rec.bpm_confidence is None


def test_probelauf_schreibt_nichts(tmp_path):
    audio = _kick_wav(tmp_path / "probe.wav", 155.0)
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        _row(db, audio, bpm=152.0, bpm_source="audio")
        res = backfill_bpm(db, dry_run=True)
        rec = db.get_track_by_path(audio)
    assert res["would_set"] == 1 and res["set"] == 0
    assert rec.bpm == 152.0 and rec.bpm_confidence is None


def test_audit_file_speichert_die_bpm_konfidenz(tmp_path):
    audio = tmp_path / "Ueberrest - Surrender.wav"
    audio.write_bytes(b"dummy")
    report = {"file": audio.name, "codec": "pcm_s16le", "ext": "wav", "bitrate_kbps": 1411,
              "sample_rate": 44100, "cutoff_hz": 21000, "ok": True, "reason": "", "duration": 200.0}
    analyse = {"bpm": 157.0, "bpm_confidence": 0.8, "bpm_octave": 1,
               "key_camelot": "8A", "key_name": "A minor"}
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        with patch("sc_digger.audit.check_file", return_value=report), \
             patch("sc_digger.audit.read_existing_tags",
                   return_value={"bpm": None, "key": None, "artist": None, "title": None}), \
             patch("sc_digger.audit.analyze_track", return_value=analyse):
            audit_file(audio, Config.load(ROOT / "config.yaml"), db)
        rec = db.get_track_by_path(audio)
        assert rec.bpm == 157.0
        assert rec.bpm_confidence == pytest.approx(0.8)
        assert db.tracks_with_legacy_bpm() == []


def test_wartungsaktion_backfill_bpm(tmp_path):
    track_db = tmp_path / "tracks.sqlite"
    cfg = Config(raw={
        "state": {"db_path": str(tmp_path / "seen.sqlite"), "track_db_path": str(track_db),
                  "health_db_path": str(tmp_path / "seen.sqlite")},
        "download": {"inbox_dir": str(tmp_path / "inbox"), "collection_dir": str(tmp_path / "Schranz")},
        "search": {"bpm_min": 140, "bpm_max": 170},
    })
    with TrackDB(track_db) as db:
        _row(db, tmp_path / "Schranz" / "fehlt.wav", bpm=161.5, bpm_source="audio")

    assert action_labels()["backfill-bpm"] == "Alte BPM-Werte neu messen"
    msg = default_runners(cfg)["backfill-bpm"]()
    assert msg == "BPM: 0 korrigiert, 0 bestätigt, 1 fehlgeschlagen"
