"""Unit-Tests für sc_digger/structure.py."""
from pathlib import Path
import wave
import numpy as np
import pytest

from sc_digger import structure as st
from sc_digger.structure import Segment, analyze_structure, find_first_kick

SR = 44100
BEAT = 0.4


def _kick() -> np.ndarray:
    t = np.arange(int(0.25 * SR)) / SR
    freq = 50 + 70 * np.exp(-t / 0.03)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    return 0.9 * np.sin(phase) * np.exp(-t / 0.08)


def _make_wav(path: Path, seconds: float, with_kick: bool = True) -> Path:
    n = int(seconds * SR)
    out = np.zeros(n)
    if with_kick:
        k = _kick()
        for i in range(int(round(seconds / BEAT))):
            start = int(i * BEAT * SR)
            out[start : start + len(k)] += k[: n - start]
    pcm = (np.clip(out, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


def test_durchgehender_kick_hat_nur_main(tmp_path):
    p = _make_wav(tmp_path / "durchgehend.wav", seconds=20.0, with_kick=True)
    fk = find_first_kick(p)
    assert fk == pytest.approx(0.0, abs=0.05)
    segs = analyze_structure(p)
    assert len(segs) == 1
    assert segs[0].kind == "main"
    assert segs[0].start_s == 0.0
    assert segs[0].end_s == pytest.approx(20.0, abs=0.2)


def test_zwei_breakdowns(tmp_path):
    # 5s Kick, 7s Stille, 5s Kick, 7s Stille, 5s Kick = 29s
    rng = np.random.default_rng(2)
    k = _kick()

    def _sec(dur, kick):
        n = int(dur * SR)
        arr = np.zeros(n)
        if kick:
            for i in range(int(round(dur / BEAT))):
                st_idx = int(i * BEAT * SR)
                arr[st_idx : st_idx + len(k)] += k[: n - st_idx]
        return arr

    audio = np.concatenate([
        _sec(5.0, True),
        _sec(7.0, False),
        _sec(5.0, True),
        _sec(7.0, False),
        _sec(5.0, True),
    ])
    p = tmp_path / "zwei_breaks.wav"
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())

    segs = analyze_structure(p)
    kinds = [s.kind for s in segs]
    assert kinds == ["main", "breakdown", "main", "breakdown", "main"]


def test_cli_ohne_argumente(capsys):
    assert st.main([]) == 1
    assert "Verwendung" in capsys.readouterr().out


def test_cli_track_ohne_kick(tmp_path, capsys):
    p = _make_wav(tmp_path / "stille.wav", seconds=5.0, with_kick=False)
    assert st.main([str(p)]) == 0
    out = capsys.readouterr().out
    assert "kein Kick gefunden" in out
