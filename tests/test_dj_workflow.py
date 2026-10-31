"""Regressionstests fuer den DJ-Workflow: harmonische Sortierung und Export.

Deckt die Kette ab, die im Review vorgeschlagen wird:
  Audio -> Analyse (BPM/Camelot + Konfidenz) -> harmonische Reihenfolge -> M3U8/CSV/Report

Bewusst auf synthetischem Material mit bekannter Tonart, damit der Test nicht von
einer Musikbibliothek abhaengt. Laeuft ohne librosa/numba.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from sc_digger import analysis as A
from sc_digger import dj_export as D

SR = 44100


# ------------------------------------------------------------------ Hilfsmittel
def _written_pad(path: Path, root_pc: int, mode: int, bpm: float = 155.0,
                 seconds: float = 30.0, sr: int = SR) -> Path:
    """Erzeugt einen Track mit eindeutiger Kadenz (i-iv-v-i bzw. I-IV-V-I)."""
    import subprocess

    tbl = np.sin(2 * np.pi * np.arange(8192) / 8192)
    beat = 60.0 / bpm
    bar = beat * 4
    prog = [0, 5, 7, 0]
    n = int(seconds * sr)
    buf = np.zeros(n, dtype=np.float64)

    def hz(midi: float) -> float:
        return 440.0 * 2.0 ** ((midi - 69) / 12.0)

    i = 0
    while i * bar < seconds:
        s = int(i * bar * sr)
        ns = min(int(bar * sr), n - s)
        if ns <= 0:
            break
        pcs = [root_pc, (root_pc + (3 if mode == 0 else 4)) % 12, (root_pc + 7) % 12]
        deg = prog[i % len(prog)]
        freqs = [hz(48 + (p + deg) % 12) for p in pcs] + \
                [hz(60 + (p + deg) % 12) for p in pcs]
        t = np.arange(ns)
        seg = np.zeros(ns)
        for f in freqs:
            for h in range(1, 5):
                if f * h > 8000:
                    break
                ph = (t * 8192 * f * h / sr) % 8192
                i0 = ph.astype(np.int64)
                seg += (1.0 / h) * tbl[i0]
        seg /= max(1.0, len(freqs) * 1.6)
        fade = min(ns, int(sr * 0.2))
        env = np.ones(ns)
        env[:fade] = np.linspace(0, 1, fade)
        env[-fade:] = np.linspace(1, 0, fade)
        buf[s:s + ns] += seg * env
        i += 1

    # Kick auf jedem Beat, damit das Tempo messbar ist
    kick_len = int(0.25 * sr)
    kt = np.arange(kick_len) / sr
    kick = (np.sin(2 * np.pi * np.cumsum(150 * np.exp(-kt * 34) + 48) / sr)
            * np.exp(-kt * 26)).astype(np.float64)
    k = 0
    while k * beat < seconds:
        s = int(k * beat * sr)
        e = min(n, s + kick_len)
        buf[s:e] += kick[: e - s] * 1.6
        k += 1

    buf /= float(np.max(np.abs(buf))) or 1.0
    raw = path.with_suffix(".f32")
    raw.write_bytes((buf * 0.9).astype(np.float32).tobytes())
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(sr),
                    "-ac", "1", "-i", str(raw), str(path)], check=True)
    raw.unlink()
    return path


def _entry(name: str, bpm: float | None, key: str | None, margin: float = 0.2) -> D.Entry:
    return D.Entry(path=Path(f"/x/{name}.wav"), artist="A", title=name,
                   bpm=bpm, bpm_confidence=0.8, camelot=key, key_margin=margin)


# ------------------------------------------------------------------ Camelot
def test_parse_camelot_valid_and_invalid():
    assert D.parse_camelot("5A") == (5, "A")
    assert D.parse_camelot("12b") == (12, "B")
    assert D.parse_camelot("13A") is None
    assert D.parse_camelot("5C") is None
    assert D.parse_camelot(None) is None
    assert D.parse_camelot("") is None


@pytest.mark.parametrize("a,b,expect", [
    ("5A", "5A", "gleich"),
    ("5A", "6A", "rad_pm1"),
    ("5A", "4A", "rad_pm1"),
    ("12A", "1A", "rad_pm1"),      # Ueberlauf am Rad
    ("1A", "12A", "rad_pm1"),
    ("5A", "5B", "relativ"),
    ("5A", "7A", "boost2"),
    ("5A", "8A", "boost3"),
    ("5A", "11A", "fern"),
    ("5A", None, "unbekannt"),
])
def test_harmonic_relation(a, b, expect):
    assert D.harmonic_relation(a, b) == expect


def test_relative_major_minor_is_not_energy_boost():
    # 8A (Am) -> 8B (C) ist die relative Dur, nicht "2 Schritte auf dem Rad"
    assert D.harmonic_relation("8A", "8B") == "relativ"


# ------------------------------------------------------------------ Uebergaenge
def test_transition_cost_ordering():
    base = _entry("a", 155, "5A")
    same = D.transition_cost(base, _entry("b", 155, "5A"))
    neighbour = D.transition_cost(base, _entry("c", 156, "6A"))
    relative = D.transition_cost(base, _entry("d", 155, "5B"))
    far = D.transition_cost(base, _entry("e", 155, "11A"))
    assert same < neighbour < relative < far


def test_transition_cost_rejects_hard_bpm_jump():
    cost, _ = D.transition_cost(_entry("a", 155, "5A"), _entry("b", 170, "5A"))
    assert cost == float("inf")


def test_transition_cost_rejects_anything_beyond_comfort():
    """Regression: ein 5-BPM-Sprung darf nicht als bequem durchgehen.

    Ursache des Fehlers war ein Deckel auf dem BPM-Zuschlag: dadurch kostete ein
    5-BPM-Sprung nur 1.0 (Toleranzgrenze) und passierte die Segmentpruefung.
    """
    a = _entry("a", 155, "5A")
    assert D.transition_cost(a, _entry("b", 155 + D.BPM_COMFORT, "5A"))[0] < float("inf")
    for jump in (D.BPM_COMFORT + 0.5, 5.0, 8.0, 15.0):
        assert D.transition_cost(a, _entry("c", 155 + jump, "5A"))[0] == float("inf"), jump


def test_transition_cost_bpm_penalty_grows_linearly():
    a = _entry("a", 155, "5A")
    c3, _ = D.transition_cost(a, _entry("b", 158, "5A"))
    c4, _ = D.transition_cost(a, _entry("c", 159, "5A"))
    assert c3 == 0.0 and c4 == 1.0


def test_unknown_bpm_is_penalised_but_allowed():
    cost, _ = D.transition_cost(_entry("a", 155, "5A"), _entry("b", None, "5A"))
    assert cost > 0
    assert cost < D.TRANSITION_COST["fern"]


# ------------------------------------------------------------------ Sortierung
def test_ordering_is_a_permutation():
    entries = [_entry(f"t{i}", 150 + i, ["5A", "6A", "7A", "8A"][i % 4]) for i in range(12)]
    pl = D.order_harmonic(entries)
    assert len(pl.entries) == len(entries)
    assert {id(e) for e in pl.entries} == {id(e) for e in entries}


def test_every_transition_is_comfortable():
    """Kernversprechen: kein Uebergang ueberschreitet das Komfortfenster."""
    # Sanfte BPM-Spanne (1er-Schritte) -> ein einziges, durchgehendes Segment
    entries = [_entry(f"t{i}", 152 + i, "5A") for i in range(10)]
    pl = D.order_harmonic(entries)
    assert pl.segments == 1, pl.transitions
    assert len(pl.transitions) == 9
    for t in pl.transitions:
        assert t["typ"] == "uebergang"
        assert t["kosten"] <= D.MAX_TRANSITION_COST, t
        assert t["harmonisch"] in ("gleich", "rad_pm1", "relativ"), t
        assert t["dbpm"] is not None and abs(t["dbpm"]) <= D.BPM_COMFORT, t
        assert t["warnung"] == "", t


def test_transitions_prefer_harmonic_neighbours():
    """Bei freier Wahl werden Raddnachbarn genommen, nicht ferne Tonarten."""
    entries = [_entry(f"k{i}", 155, key) for i, key in
               enumerate(["5A", "5B", "6A", "7A", "11A"])]
    pl = D.order_harmonic(entries)
    inner = [t for t in pl.transitions if t["typ"] == "uebergang"]
    assert inner, pl.transitions
    for t in inner:
        assert t["harmonisch"] in ("gleich", "rad_pm1", "relativ"), t


def test_bpm_worlds_are_separated_into_segments():
    """Tracks 20 BPM auseinander duerfen nicht heimlich in einer Kette landen."""
    mixed = ([_entry(f"s{i}", 150 + i * 0.5, "5A") for i in range(4)]
             + [_entry(f"f{i}", 170 + i * 0.5, "5A") for i in range(4)])
    pl = D.order_harmonic(mixed)
    assert len(pl.entries) == 8
    assert pl.segments == 2, pl.transitions
    cuts = [t for t in pl.transitions if t["typ"] == "schnitt"]
    assert len(cuts) == 1
    assert cuts[0]["warnung"].startswith("Segmentgrenze")
    for t in pl.transitions:
        if t["typ"] == "uebergang":
            assert abs(t["dbpm"]) <= D.BPM_COMFORT, t


def test_ordering_single_and_empty():
    empty = D.order_harmonic([])
    assert empty.entries == []
    assert empty.segments == 0
    one = [_entry("only", 155, "5A")]
    pl = D.order_harmonic(one)
    assert len(pl.entries) == 1
    assert pl.transitions == []


def test_session_starts_with_the_faster_segment():
    """Ein Set startet druckvoll: erstes Segment hat das hoechste Durchschnitts-BPM."""
    mixed = ([_entry(f"s{i}", 150 + i, "5A") for i in range(3)]
             + [_entry(f"f{i}", 168 + i, "5A") for i in range(3)])
    pl = D.order_harmonic(mixed)
    bpms = [e.bpm for e in pl.entries if e.bpm is not None]
    assert bpms[0] >= 168, bpms
    assert min(bpms[:3]) >= 168


def test_high_confidence_track_is_an_anchor():
    good = _entry("g", 155, "5A", margin=0.2)
    weak_key = _entry("wk", 155, "5A", margin=0.005)
    weak_bpm = _entry("wb", 155, "5A")
    weak_bpm.bpm_confidence = 0.2
    assert good.is_anchor
    assert not weak_key.is_anchor
    assert not weak_bpm.is_anchor


# ------------------------------------------------------------------ Export
def test_write_outputs_and_reload(tmp_path):
    entries = [_entry(f"t{i}", 152 + i, ["5A", "6A", "7A"][i % 3]) for i in range(6)]
    for e in entries:
        e.path = tmp_path / f"{e.title}.wav"
        e.path.write_bytes(b"x")
        e.duration_s = 300.0
    pl = D.order_harmonic(entries)

    m3u = D.write_m3u8(pl, tmp_path / "set.m3u8")
    csv_path = D.write_csv(pl, tmp_path / "set.csv")
    rep = D.write_report(entries, pl, tmp_path / "set_report.txt")

    text = m3u.read_text(encoding="utf-8")
    assert text.startswith("#EXTM3U")
    assert text.count("#EXTINF") == 6
    assert "BPM" in text
    assert "# Segment 1" in text

    reloaded = D.load_entries(csv_path)
    assert len(reloaded) == 6
    assert [e.bpm for e in reloaded] == [round(e.bpm, 1) for e in pl.entries]
    assert [e.camelot for e in reloaded] == [e.camelot for e in pl.entries]

    rtext = rep.read_text(encoding="utf-8")
    assert "Tracks insgesamt" in rtext
    assert "Harmonische Verteilung" in rtext
    assert "Segmente" in rtext


# ------------------------------------------------------------------ End-to-End
@pytest.mark.slow
def test_end_to_end_analysis_to_playlist(tmp_path):
    """Echte Audiodateien -> Analyse -> Playlist. Prueft die ganze Kette.

    Die drei Faelle sind bewusst harmonisch verwandt (Cm 5A, Fm 4A, A#m 3A) und
    liegen im selben Tempo, damit eine durchgehende Kette entstehen kann.
    """
    # (name, root_pc, mode, bpm, erwarteter Camelot-Key)
    cases = [("a", 0, 0, 155.0, "5A"),     # Cm
             ("b", 5, 0, 155.0, "4A"),     # Fm  -> Raddnachbar von 5A
             ("c", 10, 0, 156.0, "3A")]    # A#m -> Raddnachbar von 4A
    entries: list[D.Entry] = []
    for name, root, mode, bpm, want_key in cases:
        p = _written_pad(tmp_path / f"{name}.wav", root, mode, bpm=bpm)
        r = A.analyze_track(p)
        assert r["bpm"] is not None, r
        assert abs(r["bpm"] - bpm) <= 2.0, (name, r["bpm"])
        assert r["key_camelot"] == want_key, (name, r["key_camelot"], want_key)
        entries.append(D.Entry(path=p, artist="T", title=name, bpm=r["bpm"],
                               bpm_confidence=r["bpm_confidence"],
                               camelot=r["key_camelot"], key_name=r["key_name"],
                               key_margin=r["key_margin"]))

    pl = D.order_harmonic(entries)
    assert len(pl.entries) == 3
    inner = [t for t in pl.transitions if t["typ"] == "uebergang"]
    assert len(inner) == 2, pl.transitions
    assert all(t["harmonisch"] in ("gleich", "rad_pm1", "relativ") for t in inner), inner
    assert all(abs(t["dbpm"]) <= D.BPM_COMFORT for t in inner), inner


def test_end_to_end_writes_playlist(tmp_path):
    entries = []
    for name, bpm, key in (("a", 155.0, "5A"), ("b", 156.0, "4A")):
        p = _written_pad(tmp_path / f"{name}.wav", 0 if name == "a" else 5, 0, bpm=bpm)
        entries.append(D.Entry(path=p, artist="T", title=name, bpm=bpm,
                               bpm_confidence=0.9, camelot=key, key_margin=0.2))
    pl = D.order_harmonic(entries)
    out = D.write_m3u8(pl, tmp_path / "set.m3u8")
    assert out.read_text(encoding="utf-8").startswith("#EXTM3U")


def test_key_detection_takes_the_sample_rate_explicitly():
    """Regression: eine implizite Abtastrate hat die Tonart schon einmal verschoben."""
    import inspect
    # Die Tonartbestimmung auf Audiodaten verlangt die Rate und respektiert sie
    assert "sr" in inspect.signature(A._detect_key_from_audio).parameters


def test_resample_preserves_pitch_class(tmp_path):
    """440-Hz-Ton muss nach Resampling in der Chroma auf A bleiben."""
    t = np.arange(SR * 5) / SR
    tone = (0.5 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    for target in (A.SR_CHROMA, 22050):
        ys = A.resample(tone, SR, target)
        ch, w = A.chroma(ys, sr=target)
        m = np.average(ch, axis=1, weights=w)
        assert int(np.argmax(m)) == 9, (target, np.argmax(m))
