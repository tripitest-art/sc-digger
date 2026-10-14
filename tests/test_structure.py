"""Unit-Tests für sc_digger/structure.py."""
from pathlib import Path
import wave
import numpy as np
import pytest

from sc_digger import structure as st
from sc_digger.structure import (
    Segment,
    analyze_structure,
    calculate_breakdown_ratio,
    find_drop_start,
    find_first_kick,
)

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


# --- find_drop_start / calculate_breakdown_ratio (Issue #202) ---

def test_find_drop_start_nimmt_ersten_breakdown_ende():
    segs = [
        Segment("intro", 0.0, 4.0),
        Segment("main", 4.0, 20.0),
        Segment("breakdown", 20.0, 30.0),
        Segment("main", 30.0, 50.0),
        Segment("breakdown", 50.0, 60.0),
        Segment("main", 60.0, 80.0),
    ]
    assert find_drop_start(segs) == 30.0


def test_find_drop_start_akzeptiert_string_pfad(tmp_path):
    p = _make_wav(tmp_path / "stille.wav", seconds=5.0, with_kick=False)
    assert find_drop_start(str(p)) is None


def test_find_drop_start_bei_kaputter_segmentquelle():
    # Keine Sequenz, kein Pfad (z. B. int) -> defensiv None, wirft nicht.
    assert find_drop_start(123) is None  # type: ignore[arg-type]


def test_calculate_breakdown_ratio_rundet_auf_vier_nachkommastellen():
    segs = [
        Segment("intro", 0.0, 10.0),
        Segment("main", 10.0, 40.0),
        Segment("breakdown", 40.0, 50.0),
        Segment("outro", 50.0, 60.0),
    ]
    # Hauptteil = 50 - 10 = 40, Breakdown = 10 -> 0.25
    assert calculate_breakdown_ratio(segs) == 0.25


def test_calculate_breakdown_ratio_ignoriert_breakdown_ausserhalb_hauptteil():
    # Breakdown liegt vor Intro-Ende -> zählt nicht.
    segs = [
        Segment("intro", 0.0, 20.0),
        Segment("breakdown", 5.0, 10.0),
        Segment("main", 20.0, 50.0),
        Segment("outro", 50.0, 60.0),
    ]
    assert calculate_breakdown_ratio(segs) == 0.0


def test_calculate_breakdown_ratio_bei_kaputter_segmentquelle():
    assert calculate_breakdown_ratio(123) == 0.0  # type: ignore[arg-type]
