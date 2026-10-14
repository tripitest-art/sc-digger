from pathlib import Path
import wave
import numpy as np
import pytest

from sc_digger.stems import (
    Breakdown,
    Drop,
    StemAnalysisResult,
    classify_breakdown_type,
    find_subbass_drop,
    analyze_stems,
)


def _write_synth_wav(path: Path, data: np.ndarray, sr: int = 22050) -> Path:
    """Schreibt ein 1D-Numpy-Array als 16-Bit Mono-WAV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    int16_data = (np.clip(data, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(int16_data.tobytes())
    return path


def test_classify_breakdown_type_silence_und_groove(tmp_path):
    sr = 22050
    duration = 24.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    drums = np.zeros_like(t)

    rng = np.random.default_rng(100)
    noise = rng.standard_normal(len(t)) * 0.5
    # 0-6s: lauter Drum-Part
    drums[(t >= 0) & (t < 6.0)] = noise[(t >= 0) & (t < 6.0)] * 0.8
    # 6-12s: Stille-Break (fast kein Pegel)
    drums[(t >= 6.0) & (t < 12.0)] = noise[(t >= 6.0) & (t < 12.0)] * 0.02
    # 12-16s: lauter Drum-Part
    drums[(t >= 12.0) & (t < 16.0)] = noise[(t >= 12.0) & (t < 16.0)] * 0.8
    # 16-22s: Groove-Break (Hi-Hats/Percussion aktiv)
    drums[(t >= 16.0) & (t < 22.0)] = noise[(t >= 16.0) & (t < 22.0)] * 0.35
    # 22-24s: lauter Drum-Part
    drums[(t >= 22.0) & (t < 24.0)] = noise[(t >= 22.0) & (t < 24.0)] * 0.8

    p = _write_synth_wav(tmp_path / "drums.wav", drums, sr)

    kind_silence, ratio_silence = classify_breakdown_type(p, 6.0, 12.0)
    assert kind_silence == "silence"
    assert ratio_silence < 0.15

    kind_groove, ratio_groove = classify_breakdown_type(p, 16.0, 22.0)
    assert kind_groove == "groove"
    assert ratio_groove >= 0.15


def test_classify_breakdown_type_edge_cases(tmp_path):
    res_missing = classify_breakdown_type(tmp_path / "nicht_da.wav", 5.0, 10.0)
    assert res_missing == ("silence", 0.0)

    sr = 22050
    p = _write_synth_wav(tmp_path / "silent.wav", np.zeros(sr * 10), sr)
    assert classify_breakdown_type(p, 2.0, 8.0) == ("silence", 0.0)
    assert classify_breakdown_type(p, 8.0, 2.0) == ("silence", 0.0)
    assert classify_breakdown_type(p, -1.0, 5.0) == ("silence", 0.0)


def test_find_subbass_drop_echter_drop(tmp_path):
    sr = 22050
    duration = 20.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    bass = np.zeros_like(t)

    # 60 Hz Kick-Impulse alle 0.4s ab 10.0s
    kick = np.sin(2 * np.pi * 60 * np.linspace(0, 0.2, int(sr * 0.2))) * np.hanning(int(sr * 0.2))
    for kt in np.arange(10.0, 18.0, 0.4):
        idx = int(kt * sr)
        bass[idx : idx + len(kick)] += kick

    p = _write_synth_wav(tmp_path / "bass.wav", bass, sr)

    drop = find_subbass_drop(p, search_start_s=10.0, search_window_s=6.0)
    assert drop is not None
    assert isinstance(drop, Drop)
    assert abs(drop.start_s - 10.0) < 0.15
    assert drop.is_fake_drop_corrected is False
    assert drop.offset_s == 0.0
    assert drop.energy_ratio >= 0.75


def test_find_subbass_drop_fake_drop_korrektur(tmp_path):
    sr = 22050
    duration = 20.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    bass = np.zeros_like(t)

    # Gemeldeter Drop bei 8.0s, aber echter Kick setzt erst bei 11.0s ein (3.0s Versatz)
    kick = np.sin(2 * np.pi * 60 * np.linspace(0, 0.2, int(sr * 0.2))) * np.hanning(int(sr * 0.2))
    for kt in np.arange(11.0, 18.0, 0.4):
        idx = int(kt * sr)
        bass[idx : idx + len(kick)] += kick

    p = _write_synth_wav(tmp_path / "bass_fake.wav", bass, sr)

    drop = find_subbass_drop(p, search_start_s=8.0, search_window_s=6.0)
    assert drop is not None
    assert drop.is_fake_drop_corrected is True
    assert abs(drop.start_s - 11.0) < 0.2
    assert abs(drop.offset_s - 3.0) < 0.2


def test_find_subbass_drop_kein_subbass_im_suchfenster(tmp_path):
    sr = 22050
    duration = 15.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    bass = np.zeros_like(t)

    # Subbass nur ganz am Anfang (0-3s), ab 8.0s nichts
    kick = np.sin(2 * np.pi * 60 * np.linspace(0, 0.2, int(sr * 0.2)))
    bass[: len(kick)] += kick

    p = _write_synth_wav(tmp_path / "bass_none.wav", bass, sr)
    drop = find_subbass_drop(p, search_start_s=8.0, search_window_s=5.0)
    assert drop is None


def test_find_subbass_drop_mit_downbeats(tmp_path):
    sr = 22050
    duration = 20.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    bass = np.zeros_like(t)

    kick = np.sin(2 * np.pi * 60 * np.linspace(0, 0.2, int(sr * 0.2)))
    # Kick schlägt bei 12.0s ein
    idx = int(12.0 * sr)
    bass[idx : idx + len(kick)] += kick

    p = _write_synth_wav(tmp_path / "bass_db.wav", bass, sr)
    downbeats = [8.0, 10.0, 12.0, 14.0]

    drop = find_subbass_drop(p, search_start_s=8.0, downbeats=downbeats, search_window_s=6.0)
    assert drop is not None
    assert drop.start_s == 12.0
    assert drop.is_fake_drop_corrected is True
    assert abs(drop.offset_s - 4.0) < 0.05


def test_analyze_stems_kombiniert(tmp_path):
    sr = 22050
    duration = 35.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)

    drums = np.zeros_like(t)
    bass = np.zeros_like(t)
    rng = np.random.default_rng(200)
    noise = rng.standard_normal(len(t)) * 0.5
    kick = np.sin(2 * np.pi * 60 * np.linspace(0, 0.15, int(sr * 0.15))) * np.hanning(int(sr * 0.15))

    # Kicks: 0-5.5s (Kicks alle 0.5s)
    for kt in np.arange(0.5, 5.6, 0.5):
        idx = int(kt * sr)
        bass[idx : idx + len(kick)] += kick
    drums[(t >= 0) & (t < 5.6)] = noise[(t >= 0) & (t < 5.6)] * 0.8

    # Pause 1: 5.6-14.0s (Silence Break: > 8s Pause)
    drums[(t >= 5.6) & (t < 14.0)] = noise[(t >= 5.6) & (t < 14.0)] * 0.01

    # Kicks: 14.0-19.5s
    for kt in np.arange(14.0, 19.6, 0.5):
        idx = int(kt * sr)
        bass[idx : idx + len(kick)] += kick
    drums[(t >= 14.0) & (t < 19.6)] = noise[(t >= 14.0) & (t < 19.6)] * 0.8

    # Pause 2: 19.6-28.0s (Groove Break: > 8s Pause)
    drums[(t >= 19.6) & (t < 28.0)] = noise[(t >= 19.6) & (t < 28.0)] * 0.35

    # Kicks: 28.0-35.0s
    for kt in np.arange(28.0, 35.0, 0.5):
        idx = int(kt * sr)
        bass[idx : idx + len(kick)] += kick
    drums[(t >= 28.0) & (t < 35.0)] = noise[(t >= 28.0) & (t < 35.0)] * 0.8

    p_bass = _write_synth_wav(tmp_path / "track_bass.wav", bass, sr)
    p_drums = _write_synth_wav(tmp_path / "track_drums.wav", drums, sr)

    res = analyze_stems(p_bass, p_drums, min_pause_s=6.0)
    assert isinstance(res, StemAnalysisResult)
    assert res.first_kick_s is not None
    assert abs(res.first_kick_s - 0.5) < 0.2

    assert len(res.breakdowns) == 2
    assert res.breakdowns[0].kind == "silence"
    assert res.breakdowns[1].kind == "groove"

    assert len(res.drops) == 2
    assert abs(res.drops[0].start_s - 14.0) < 0.3
    assert abs(res.drops[1].start_s - 28.0) < 0.3


def test_analyze_stems_fehlerfaelle(tmp_path):
    res = analyze_stems(tmp_path / "fehlt_b.wav", tmp_path / "fehlt_d.wav")
    assert res.breakdowns == []
    assert res.drops == []
    assert res.first_kick_s is None
