import subprocess
from pathlib import Path
from unittest.mock import patch
import pytest
from sc_digger.analysis import analyze_track, detect_bpm, detect_key


def _generate_synthetic_track(path: Path, freq: float = 440.0, bpm: float = 155.0, duration: int = 15) -> Path:
    # Erzeugt Audio mit pulsierender Amplitude (Tremolo) bei exakt bpm und gegebener Frequenz (440 Hz = A4 = Am / 8A)
    # Modulation mit sinusförmigem Tremolo der Frequenz bpm/60 Hz
    tremolo_freq = bpm / 60.0
    lavfi = f"sine=frequency={freq}:duration={duration},tremolo=f={tremolo_freq}:d=0.9"
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", lavfi,
        "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", str(path),
    ]
    subprocess.run(cmd, check=True)
    return path


def test_analyze_track_returns_full_metadata_and_confidence(tmp_path):
    track = _generate_synthetic_track(tmp_path / "track_155.wav", freq=440.0, bpm=155.0)
    res = analyze_track(track)
    assert isinstance(res, dict)
    assert res["bpm"] is not None and abs(res["bpm"] - 155.0) <= 2.0
    assert "bpm_confidence" in res and res["bpm_confidence"] > 0.0
    assert "bpm_octave" in res
    assert res["key_camelot"] is not None
    assert res["key_name"] is not None
    assert "key_confidence" in res
    assert "key_margin" in res


def test_analyze_track_snap_to_octave(tmp_path):
    # Track mit 79 BPM (Halbzeit-Puls, z. B. Kick nur auf 1 und 3)
    track_79 = _generate_synthetic_track(tmp_path / "track_79.wav", freq=440.0, bpm=79.0)
    # Ohne snap_to liegt der Wert bei ~79 BPM
    res_raw = analyze_track(track_79)
    assert res_raw["bpm"] is not None and abs(res_raw["bpm"] - 79.0) <= 2.0

    # Mit snap_to=(150, 165) wird der Wert in das 155er Fenster geholt (~158 BPM)
    res_snapped = analyze_track(track_79, snap_to=(150.0, 165.0))
    assert res_snapped["bpm"] is not None and abs(res_snapped["bpm"] - 158.0) <= 2.0
    assert res_snapped["bpm_octave"] == 2


def test_analyze_track_uses_single_ffmpeg_decode(tmp_path):
    track = _generate_synthetic_track(tmp_path / "single_decode.wav", freq=440.0, bpm=155.0)
    with patch("subprocess.run", wraps=subprocess.run) as mock_run:
        res = analyze_track(track)
        assert res["bpm"] is not None
        assert res["key_camelot"] is not None
        # Es darf für analyze_track höchstens 1 ffmpeg-Aufruf zum Decodieren stattfinden (nicht 2 oder 3)
        ffmpeg_calls = [
            call for call in mock_run.call_args_list
            if call[0] and isinstance(call[0][0], list) and "ffmpeg" in call[0][0][0]
        ]
        assert len(ffmpeg_calls) == 1


def test_backward_compatibility_detect_bpm_and_key(tmp_path):
    track = _generate_synthetic_track(tmp_path / "compat.wav", freq=440.0, bpm=155.0)
    bpm = detect_bpm(track)
    assert isinstance(bpm, float) and abs(bpm - 155.0) <= 2.0

    key_res = detect_key(track)
    assert isinstance(key_res, tuple) and len(key_res) == 2
    assert isinstance(key_res[0], str) and isinstance(key_res[1], str)
