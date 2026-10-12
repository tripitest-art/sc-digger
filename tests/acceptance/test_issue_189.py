"""Akzeptanztests: Strukturanalyse (Kick-Präsenz, Segmente, erster Kick)."""
import re
import wave
from dataclasses import FrozenInstanceError
from pathlib import Path

import numpy as np
import pytest

from sc_digger import structure as st
from sc_digger.structure import Segment, analyze_structure, find_first_kick

SR = 44100
BEAT = 0.4  # 150 BPM
BAR = 4 * BEAT
TOL_KICK = 0.05  # Sekunden
TOL_GRENZE = 0.8  # Sekunden, eine halbe Bar


def _kick() -> np.ndarray:
    t = np.arange(int(0.25 * SR)) / SR
    freq = 50 + 70 * np.exp(-t / 0.03)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    return 0.9 * np.sin(phase) * np.exp(-t / 0.08)


def _hat(rng) -> np.ndarray:
    n = int(0.03 * SR)
    noise = np.diff(rng.standard_normal(n + 1))  # einfacher Hochpass
    return 0.15 * noise * np.exp(-np.arange(n) / (0.008 * SR))


def _section(kind: str, seconds: float, rng) -> np.ndarray:
    n = int(seconds * SR)
    out = np.zeros(n)
    if kind == "pad":
        t = np.arange(n) / SR
        return 0.2 * (np.sin(2 * np.pi * 220 * t) + np.sin(2 * np.pi * 330 * t))
    for i in range(int(seconds / 0.2)):  # Hi-Hats auf jeder Achtel
        h = _hat(rng)
        start = int(i * 0.2 * SR)
        out[start:start + len(h)] += h[: n - start]
    if kind == "kick":
        k = _kick()
        for i in range(int(round(seconds / BEAT))):
            start = int(i * BEAT * SR)
            out[start:start + len(k)] += k[: n - start]
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


# 6,4 s Intro ohne Kick, 19,2 s Kick, 12,8 s Breakdown (8 Bars), 19,2 s Kick, 6,4 s Outro
VOLL = [("hats", 6.4), ("kick", 19.2), ("pad", 12.8), ("kick", 19.2), ("hats", 6.4)]


def test_segment_ist_unveraenderlich():
    s = Segment("main", 0.0, 1.0)
    assert (s.kind, s.start_s, s.end_s) == ("main", 0.0, 1.0)
    with pytest.raises(FrozenInstanceError):
        s.kind = "intro"  # type: ignore[misc]


def test_konstanten():
    assert st.MIN_BREAKDOWN_S == 6.0
    assert st.MIN_EDGE_S == 2.0


def test_erster_kick_nach_dem_intro(tmp_path):
    p = make_track(tmp_path / "t.wav", VOLL)
    assert find_first_kick(p) == pytest.approx(6.4, abs=TOL_KICK)


def test_erster_kick_ausserhalb_des_suchfensters_ist_none(tmp_path):
    p = make_track(tmp_path / "t.wav", VOLL)
    assert find_first_kick(p, search_s=5.0) is None
    assert find_first_kick(p, search_s=10.0) == pytest.approx(6.4, abs=TOL_KICK)


def test_erster_kick_bei_sekunde_null(tmp_path):
    p = make_track(tmp_path / "t.wav", [("kick", 19.2), ("pad", 12.8), ("kick", 19.2)])
    assert find_first_kick(p) == pytest.approx(0.0, abs=TOL_KICK)


def test_segmente_eines_vollstaendigen_tracks(tmp_path):
    p = make_track(tmp_path / "t.wav", VOLL)
    segs = analyze_structure(p)
    assert [s.kind for s in segs] == ["intro", "main", "breakdown", "main", "outro"]
    soll = [0.0, 6.4, 25.6, 38.4, 57.6, 64.0]
    ist = [segs[0].start_s] + [s.end_s for s in segs]
    for a, b in zip(ist, soll):
        assert a == pytest.approx(b, abs=TOL_GRENZE)


def test_segmente_sind_lueckenlos_und_geordnet(tmp_path):
    segs = analyze_structure(make_track(tmp_path / "t.wav", VOLL))
    assert segs[0].start_s == 0.0
    for a, b in zip(segs, segs[1:]):
        assert b.start_s == a.end_s
    assert all(s.end_s > s.start_s for s in segs)
    assert segs[-1].end_s == pytest.approx(64.0, abs=0.2)


def test_kurze_pause_ist_kein_breakdown(tmp_path):
    layout = [("hats", 6.4), ("kick", 9.6), ("hats", BAR), ("kick", 9.6), ("pad", 12.8),
              ("kick", 19.2), ("hats", 6.4)]
    segs = analyze_structure(make_track(tmp_path / "t.wav", layout))
    assert [s.kind for s in segs] == ["intro", "main", "breakdown", "main", "outro"]
    assert segs[1].end_s == pytest.approx(6.4 + 9.6 + BAR + 9.6, abs=TOL_GRENZE)


def test_ohne_intro_und_outro(tmp_path):
    segs = analyze_structure(make_track(tmp_path / "t.wav", [("kick", 19.2), ("pad", 12.8), ("kick", 19.2)]))
    assert [s.kind for s in segs] == ["main", "breakdown", "main"]


def test_track_ohne_kick_liefert_nichts(tmp_path):
    p = make_track(tmp_path / "t.wav", [("pad", 20.0)])
    assert find_first_kick(p) is None
    assert analyze_structure(p) == []


def test_unlesbare_dateien_werfen_nicht(tmp_path):
    kaputt = tmp_path / "kaputt.wav"
    kaputt.write_bytes(b"kein audio")
    for p in (tmp_path / "gibt-es-nicht.wav", kaputt):
        assert find_first_kick(p) is None
        assert analyze_structure(p) == []


def test_cli_gibt_kick_und_segmente_aus(tmp_path, capsys):
    p = make_track(tmp_path / "t.wav", VOLL)
    assert st.main([str(p)]) == 0
    out = capsys.readouterr().out
    m = re.search(r"erster Kick: (\d+\.\d+) s", out)
    assert m and float(m.group(1)) == pytest.approx(6.4, abs=TOL_KICK)
    kinds = re.findall(r"^(intro|main|breakdown|outro)\s", out, flags=re.M)
    assert kinds == ["intro", "main", "breakdown", "main", "outro"]


def test_cli_meldet_fehlende_datei(tmp_path, capsys):
    assert st.main([str(tmp_path / "gibt-es-nicht.wav")]) == 1
    assert "nicht gefunden" in capsys.readouterr().out
