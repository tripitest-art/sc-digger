"""Unit-Tests für das Stems-Modul (Subbass-Drop- und Breakdown-Analyse).

Alle Tests arbeiten rein synthetisch (WAV per numpy, lokal geschrieben); es gibt
keinen Netzwerk- oder Inferenzzugriff.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import wave

import numpy as np
import pytest

from sc_digger.stems import (
    Breakdown,
    Drop,
    StemAnalysisResult,
    analyze_stems,
    classify_breakdown_type,
    find_subbass_drop,
)

SR = 22050


def _write_wav(path: Path, data: np.ndarray, sr: int = SR) -> Path:
    """Schreibt ein 1D-Numpy-Array als 16-Bit Mono-WAV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    int16_data = (np.clip(data, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(int16_data.tobytes())
    return path


def _bass_kicks(kick_times, duration: float, length_s: float = 0.15) -> np.ndarray:
    """Subbass-Stem mit 60-Hz-Kicks (Hanning-Hüllkurve) an den genannten Zeiten."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    bass = np.zeros_like(t)
    kick = (
        np.sin(2 * np.pi * 60 * np.linspace(0, length_s, int(SR * length_s)))
        * np.hanning(int(SR * length_s))
    )
    for kt in kick_times:
        i = int(kt * SR)
        bass[i : i + len(kick)] += kick
    return bass


def _drums_noise(duration: float, level: float = 0.8, seed: int = 7) -> np.ndarray:
    """Drums-Stem aus gleichmäßigem Rauschen mit vorgegebenem Pegel."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    rng = np.random.default_rng(seed)
    return rng.standard_normal(len(t)) * 0.5 * level


def test_classify_breakdown_type_eigener_schwellwert(tmp_path):
    duration = 24.0
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    rng = np.random.default_rng(42)
    noise = rng.standard_normal(len(t)) * 0.5
    drums = np.zeros_like(t)
    drums[(t >= 0) & (t < 6.0)] = noise[(t >= 0) & (t < 6.0)] * 0.8
    drums[(t >= 6.0) & (t < 12.0)] = noise[(t >= 6.0) & (t < 12.0)] * 0.02
    drums[(t >= 12.0) & (t < 16.0)] = noise[(t >= 12.0) & (t < 16.0)] * 0.8
    drums[(t >= 16.0) & (t < 22.0)] = noise[(t >= 16.0) & (t < 22.0)] * 0.35
    drums[(t >= 22.0) & (t < 24.0)] = noise[(t >= 22.0) & (t < 24.0)] * 0.8
    p = _write_wav(tmp_path / "drums.wav", drums)

    kind, ratio = classify_breakdown_type(p, 16.0, 22.0, groove_threshold=0.7)
    assert kind == "silence"
    assert ratio < 0.7


def test_classify_breakdown_type_zeitraum_ueber_dateiende(tmp_path):
    p = _write_wav(tmp_path / "drums.wav", _drums_noise(8.0))

    kind, ratio = classify_breakdown_type(p, 2.0, 1000.0)
    assert kind == "groove"
    assert ratio > 0.1  # Abschnitts-RMS kann den Gesamt-RMS leicht überschreiten


def test_classify_breakdown_type_unlesbare_datei(tmp_path):
    bad = tmp_path / "kaputt.wav"
    bad.write_bytes(b"kein audio")

    assert classify_breakdown_type(bad, 0.0, 5.0) == ("silence", 0.0)


def test_find_subbass_drop_fehlende_datei(tmp_path):
    assert find_subbass_drop(tmp_path / "fehlt.wav", 0.0) is None


def test_find_subbass_drop_bpm_ohne_wirkung(tmp_path):
    bass = _bass_kicks(np.arange(10.0, 18.0, 0.4), 20.0)
    p = _write_wav(tmp_path / "bass.wav", bass)

    ohne = find_subbass_drop(p, 10.0, search_window_s=6.0)
    mit = find_subbass_drop(p, 10.0, bpm=150.0, search_window_s=6.0)
    assert ohne is not None
    assert ohne == mit


def test_find_subbass_drop_downbeat_ohne_energie_fallback(tmp_path):
    # Der einzige Downbeat (8.0s) liegt vor dem Kick -> Rückfall auf den Frame-Treffer.
    bass = _bass_kicks([10.0], 20.0)
    p = _write_wav(tmp_path / "bass.wav", bass)

    drop = find_subbass_drop(p, 8.0, downbeats=[8.0], search_window_s=6.0)
    assert drop is not None
    assert abs(drop.start_s - 10.0) < 0.2
    assert drop.is_fake_drop_corrected is True


def test_find_subbass_drop_energieverhaeltnis_plausibel(tmp_path):
    bass = _bass_kicks(np.arange(10.0, 18.0, 0.4), 20.0)
    p = _write_wav(tmp_path / "bass.wav", bass)

    drop = find_subbass_drop(p, 10.0, search_window_s=6.0)
    assert drop is not None
    assert 0.0 < drop.energy_ratio <= 1.0
    assert drop.offset_s == 0.0
    assert drop.is_fake_drop_corrected is False


def test_analyze_stems_intro_und_outro_zaehlen_nicht(tmp_path):
    # Intro- (0-10s) und Outro-Stille (20-30s) sind keine Breakdowns.
    duration = 30.0
    bass = _bass_kicks(np.arange(10.0, 20.0, 0.5), duration)
    p_bass = _write_wav(tmp_path / "bass.wav", bass)
    p_drums = _write_wav(tmp_path / "drums.wav", _drums_noise(duration))

    res = analyze_stems(p_bass, p_drums, min_pause_s=6.0)
    assert isinstance(res, StemAnalysisResult)
    assert res.breakdowns == []
    assert res.drops == []
    assert abs(res.first_kick_s - 10.0) < 0.2


def test_analyze_stems_breakdown_felder_plausibel(tmp_path):
    duration = 30.0
    kicks = list(np.arange(0.5, 5.6, 0.5)) + list(np.arange(16.0, 25.1, 0.5))
    bass = _bass_kicks(kicks, duration)
    p_bass = _write_wav(tmp_path / "bass.wav", bass)
    p_drums = _write_wav(tmp_path / "drums.wav", _drums_noise(duration))

    res = analyze_stems(p_bass, p_drums, min_pause_s=6.0)
    assert len(res.breakdowns) == 1
    (breakdown,) = res.breakdowns
    assert isinstance(breakdown, Breakdown)
    assert breakdown.start_s < breakdown.end_s
    assert breakdown.end_s - breakdown.start_s >= 6.0
    assert 0.0 <= breakdown.drum_rms_ratio <= 1.0
    assert breakdown.kind in {"silence", "groove"}


def test_analyze_stems_rastet_drops_auf_downbeats(tmp_path):
    duration = 30.0
    kicks = list(np.arange(0.5, 5.6, 0.5)) + list(np.arange(16.0, 25.1, 0.5))
    bass = _bass_kicks(kicks, duration)
    p_bass = _write_wav(tmp_path / "bass.wav", bass)
    p_drums = _write_wav(tmp_path / "drums.wav", _drums_noise(duration))

    res = analyze_stems(
        p_bass,
        p_drums,
        downbeats=[6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0],
        min_pause_s=6.0,
    )
    assert len(res.breakdowns) == 1
    assert len(res.drops) == 1
    assert res.drops[0].start_s == 16.0


def test_analyze_stems_ohne_drums_stem(tmp_path):
    duration = 30.0
    kicks = list(np.arange(0.5, 5.6, 0.5)) + list(np.arange(16.0, 25.1, 0.5))
    bass = _bass_kicks(kicks, duration)
    p_bass = _write_wav(tmp_path / "bass.wav", bass)

    res = analyze_stems(p_bass, tmp_path / "fehlt_drums.wav", min_pause_s=6.0)
    assert len(res.breakdowns) == 1
    assert res.breakdowns[0].kind == "silence"
    assert len(res.drops) == 1


def test_analyze_stems_unlesbares_bass_stem(tmp_path):
    bad = tmp_path / "kaputt.wav"
    bad.write_bytes(b"kein audio")

    res = analyze_stems(bad, bad)
    assert res.breakdowns == []
    assert res.drops == []
    assert res.first_kick_s is None


def test_drop_ist_unveraenderlich():
    drop = Drop(start_s=1.0, energy_ratio=0.9)

    with pytest.raises(FrozenInstanceError):
        drop.start_s = 2.0  # type: ignore[misc]
