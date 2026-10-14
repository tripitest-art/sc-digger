"""Akzeptanztests: Drop-Preview und Breakdown-Anteil (ROADMAP 5.3 und 5.4)."""
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from sc_digger import preview
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config, Track
from sc_digger.preview import (
    DEFAULT_PREVIEW_DURATION_S,
    DEFAULT_RISER_S,
    extract_preview,
    find_loudest_segment,
    find_preview_start,
)
from sc_digger.schema import validate_config
from sc_digger.structure import (
    Segment,
    calculate_breakdown_ratio,
    find_drop_start,
)

ROOT = Path(__file__).resolve().parents[2]
SR = 44100
BEAT = 0.4  # 150 BPM
BAR = 4 * BEAT
TOL_GRENZE = 0.8  # Sekunden, halbe Bar Toleranz für FFT/Grenzenerkennung


def _kick() -> np.ndarray:
    t = np.arange(int(0.25 * SR)) / SR
    freq = 50 + 70 * np.exp(-t / 0.03)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    return 0.9 * np.sin(phase) * np.exp(-t / 0.08)


def _hat(rng: np.random.Generator) -> np.ndarray:
    n = int(0.03 * SR)
    noise = np.diff(rng.standard_normal(n + 1))  # einfacher Hochpass
    return 0.15 * noise * np.exp(-np.arange(n) / (0.008 * SR))


def _section(kind: str, seconds: float, rng: np.random.Generator) -> np.ndarray:
    n = int(seconds * SR)
    out = np.zeros(n)
    if kind == "pad":
        t = np.arange(n) / SR
        return 0.2 * (np.sin(2 * np.pi * 220 * t) + np.sin(2 * np.pi * 330 * t))
    for i in range(int(seconds / 0.2)):  # Hi-Hats auf jeder Achtel
        h = _hat(rng)
        start = int(i * 0.2 * SR)
        out[start : start + len(h)] += h[: n - start]
    if kind == "kick":
        k = _kick()
        for i in range(int(round(seconds / BEAT))):
            start = int(i * BEAT * SR)
            out[start : start + len(k)] += k[: n - start]
    return out


def make_track(path: Path, layout: list[tuple[str, float]]) -> Path:
    rng = np.random.default_rng(1)
    audio = np.concatenate([_section(kind, sec, rng) for kind, sec in layout])
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


# 6,4 s Intro ohne Kick, 19,2 s Kick, 12,8 s Breakdown (8 Bars), 19,2 s Kick, 6,4 s Outro = 64 s
VOLL = [("hats", 6.4), ("kick", 19.2), ("pad", 12.8), ("kick", 19.2), ("hats", 6.4)]


# --- Tests für structure.py: find_drop_start & calculate_breakdown_ratio ---

def test_find_drop_start_aus_segmenten():
    segs = [
        Segment("intro", 0.0, 6.4),
        Segment("main", 6.4, 25.6),
        Segment("breakdown", 25.6, 38.4),
        Segment("main", 38.4, 57.6),
        Segment("outro", 57.6, 64.0),
    ]
    assert find_drop_start(segs) == pytest.approx(38.4, abs=0.001)


def test_find_drop_start_aus_audiodatei(tmp_path):
    p = make_track(tmp_path / "voll.wav", VOLL)
    assert find_drop_start(p) == pytest.approx(38.4, abs=TOL_GRENZE)


def test_find_drop_start_ohne_breakdown_liefert_none(tmp_path):
    segs_ohne = [Segment("intro", 0.0, 6.4), Segment("main", 6.4, 40.0)]
    assert find_drop_start(segs_ohne) is None

    p_ohne = make_track(tmp_path / "durchgehend.wav", [("kick", 25.0)])
    assert find_drop_start(p_ohne) is None


def test_find_drop_start_bei_leeren_oder_defekten_dateien(tmp_path):
    assert find_drop_start([]) is None
    assert find_drop_start(tmp_path / "fehlt.wav") is None

    kaputt = tmp_path / "kaputt.wav"
    kaputt.write_bytes(b"kein audio")
    assert find_drop_start(kaputt) is None


def test_calculate_breakdown_ratio_aus_segmenten():
    # Hauptteil = 57.6 - 6.4 = 51.2 s, Breakdown = 38.4 - 25.6 = 12.8 s -> 12.8 / 51.2 = 0.25
    segs = [
        Segment("intro", 0.0, 6.4),
        Segment("main", 6.4, 25.6),
        Segment("breakdown", 25.6, 38.4),
        Segment("main", 38.4, 57.6),
        Segment("outro", 57.6, 64.0),
    ]
    assert calculate_breakdown_ratio(segs) == pytest.approx(0.25, abs=0.001)


def test_calculate_breakdown_ratio_mehrere_breakdowns():
    # Hauptteil = 85.0 - 5.0 = 80.0 s, Breakdowns = 10.0 + 10.0 = 20.0 s -> 20.0 / 80.0 = 0.25
    segs = [
        Segment("intro", 0.0, 5.0),
        Segment("main", 5.0, 25.0),
        Segment("breakdown", 25.0, 35.0),
        Segment("main", 35.0, 55.0),
        Segment("breakdown", 55.0, 65.0),
        Segment("main", 65.0, 85.0),
        Segment("outro", 85.0, 95.0),
    ]
    assert calculate_breakdown_ratio(segs) == pytest.approx(0.25, abs=0.001)


def test_calculate_breakdown_ratio_ohne_intro_und_outro():
    # Kein Intro, kein Outro: Hauptteil = 50.0 - 0.0 = 50.0 s, Breakdown = 10.0 s -> 0.20
    segs = [
        Segment("main", 0.0, 20.0),
        Segment("breakdown", 20.0, 30.0),
        Segment("main", 30.0, 50.0),
    ]
    assert calculate_breakdown_ratio(segs) == pytest.approx(0.2, abs=0.001)


def test_calculate_breakdown_ratio_aus_audiodatei(tmp_path):
    p = make_track(tmp_path / "voll.wav", VOLL)
    assert calculate_breakdown_ratio(p) == pytest.approx(0.25, abs=0.05)


def test_calculate_breakdown_ratio_ohne_breakdown(tmp_path):
    segs_ohne = [Segment("intro", 0.0, 6.4), Segment("main", 6.4, 40.0), Segment("outro", 40.0, 45.0)]
    assert calculate_breakdown_ratio(segs_ohne) == 0.0

    p_ohne = make_track(tmp_path / "durchgehend.wav", [("kick", 25.0)])
    assert calculate_breakdown_ratio(p_ohne) == 0.0


def test_calculate_breakdown_ratio_bei_leeren_oder_defekten_dateien(tmp_path):
    assert calculate_breakdown_ratio([]) == 0.0
    assert calculate_breakdown_ratio(tmp_path / "fehlt.wav") == 0.0

    kaputt = tmp_path / "kaputt.wav"
    kaputt.write_bytes(b"kein audio")
    assert calculate_breakdown_ratio(kaputt) == 0.0


# --- Tests für preview.py: find_preview_start & extract_preview ---

def test_preview_konstanten():
    assert DEFAULT_PREVIEW_DURATION_S == 20.0
    assert DEFAULT_RISER_S == 5.0


def test_find_preview_start_mit_drop(tmp_path):
    p = make_track(tmp_path / "voll.wav", VOLL)
    start = find_preview_start(p, segment_duration_s=20.0, riser_s=5.0)
    assert start == pytest.approx(33.4, abs=TOL_GRENZE)


def test_find_preview_start_konfigurierbarer_riser(tmp_path):
    p = make_track(tmp_path / "voll.wav", VOLL)
    start = find_preview_start(p, segment_duration_s=20.0, riser_s=3.0)
    assert start == pytest.approx(35.4, abs=TOL_GRENZE)


def test_find_preview_start_fallback_ohne_breakdown(tmp_path):
    p = make_track(tmp_path / "kick_only.wav", [("kick", 30.0)])
    start = find_preview_start(p, segment_duration_s=10.0)
    lautestes = find_loudest_segment(p, 10.0)
    assert start == pytest.approx(lautestes, abs=0.001)


def test_find_preview_start_bei_fehlender_datei(tmp_path):
    assert find_preview_start(tmp_path / "gibt-es-nicht.wav") == 0.0


def test_extract_preview_erzeugt_opus_an_drop_stelle(tmp_path):
    p = make_track(tmp_path / "voll.wav", VOLL)
    out_ogg = tmp_path / "preview.ogg"
    ok = extract_preview(p, out_ogg, duration_s=15.0, riser_s=5.0)
    assert ok is True
    assert out_ogg.is_file()
    assert out_ogg.stat().st_size > 500

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(out_ogg),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert float(probe.stdout.strip()) == pytest.approx(15.0, abs=1.0)


# --- Tests für models.py, db.py (Migration 7) & config.yaml / schema.py ---

def test_track_modell_hat_breakdown_ratio():
    t = Track(
        id=1, title="T", url="u", artist="A", artist_url="au",
        created_at="2026-10-12T00:00:00Z", duration_ms=64000, genre="Schranz",
        tags=[], description="", bpm=150.0, plays=10, likes=5, reposts=1,
        comments=0, downloadable=True, has_downloads_left=True,
        purchase_url=None, purchase_title=None, breakdown_ratio=0.25,
    )
    assert t.breakdown_ratio == 0.25

    t_default = Track(
        id=2, title="T2", url="u", artist="A", artist_url="au",
        created_at="2026-10-12T00:00:00Z", duration_ms=64000, genre="Schranz",
        tags=[], description="", bpm=150.0, plays=0, likes=0, reposts=0,
        comments=0, downloadable=False, has_downloads_left=False,
        purchase_url=None, purchase_title=None,
    )
    assert t_default.breakdown_ratio is None


def test_track_record_und_db_persistierung(tmp_path):
    db_file = tmp_path / "test_tracks.sqlite"
    rec = TrackRecord(
        path="/music/test.mp3",
        mtime=1234567.0,
        size=4096,
        breakdown_ratio=0.25,
    )
    assert rec.breakdown_ratio == 0.25

    with TrackDB(db_file) as db:
        saved = db.upsert_track(rec)
        assert saved.breakdown_ratio == pytest.approx(0.25)

        loaded = db.get_track_by_path("/music/test.mp3")
        assert loaded is not None
        assert loaded.breakdown_ratio == pytest.approx(0.25)


def test_config_und_schema_preview_parameter():
    cfg = Config.load(ROOT / "config.yaml")
    assert cfg["preview"]["duration_s"] == 20.0
    assert cfg["preview"]["riser_s"] == 5.0
    problems = validate_config(cfg.raw)
    assert problems == []
