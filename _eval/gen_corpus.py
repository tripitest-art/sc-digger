"""Erzeugt einen synthetischen Ground-Truth-Korpus fuer die Analyse-Benchmarks.

Jeder Case hat ein bekanntes Tempo (BPM) und eine bekannte Tonart, damit
detect_bpm/detect_key messbar (nicht nur gefuehlt) bewertet werden koennen.

Aufruf:  python _eval/gen_corpus.py [outdir]
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

SR = 44100

PITCH = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5,
         "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
CAMELOT = {
    (0, 0): "5A", (1, 0): "12A", (2, 0): "7A", (3, 0): "2A", (4, 0): "9A", (5, 0): "4A",
    (6, 0): "11A", (7, 0): "6A", (8, 0): "1A", (9, 0): "8A", (10, 0): "3A", (11, 0): "10A",
    (0, 1): "8B", (1, 1): "3B", (2, 1): "10B", (3, 1): "5B", (4, 1): "12B", (5, 1): "7B",
    (6, 1): "4B", (7, 1): "11B", (8, 1): "6B", (9, 1): "1B", (10, 1): "8B", (11, 1): "3B",
}
NAMES_MINOR = ["Cm", "C#m", "Dm", "D#m", "Em", "Fm", "F#m", "Gm", "G#m", "Am", "A#m", "Bm"]
NAMES_MAJOR = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Kadenzen mit klarer Tonika: Moll i-iv-v-i, Dur I-IV-V-I.
# Bewusst NICHT i-VI-VII-III: eine solche modale Schleife vermeidet die Tonika-
# Funktion, ist gehoermassig mehrdeutig (Parallel-Dur) und wurde hier gemessen
# mit 0/24 erkannt - ein solcher Korpus bestraft den Detektor fuer ein
# Musikbeispiel, das objektiv keine eindeutige Tonart hat.
MINOR_PROG = [0, 5, 7, 0, 0, 5, 7, 0]
MAJOR_PROG = [0, 5, 7, 0, 0, 5, 7, 0]


def hz(midi: float) -> float:
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


def _kick(n: int, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(n) / SR
    env = np.exp(-t * 26.0)
    f = 150.0 * np.exp(-t * 34.0) + 48.0
    body = np.sin(2 * np.pi * np.cumsum(f) / SR)
    click = rng.standard_normal(n) * np.exp(-t * 320.0)
    return (body * env + 0.28 * click).astype(np.float32)


def _hat(n: int, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(n) / SR
    noise = rng.standard_normal(n) * np.exp(-t * 90.0)
    # grob hochpassen: Differenzieren
    return np.diff(noise, prepend=0.0).astype(np.float32) * 0.5


def _clap(n: int, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(n) / SR
    env = np.exp(-t * 45.0) * (1 + 0.6 * np.sin(2 * np.pi * 30 * t))
    return (rng.standard_normal(n) * env).astype(np.float32) * 0.6


def _bass(n: int, f0: float) -> np.ndarray:
    t = np.arange(n) / SR
    env = np.minimum(1.0, t * 60.0) * np.exp(-t * 5.0)
    x = np.sin(2 * np.pi * f0 * t) + 0.35 * np.sin(4 * np.pi * f0 * t)
    return (np.tanh(1.8 * x) * env).astype(np.float32) * 0.5


def _pad_chord(n: int, freqs: list[float], gain: float = 1.0,
               harmonics: int = 6, cutoff: float = 3000.0) -> np.ndarray:
    """Weicher, stabiler Pad-Akkord per Wellentabellen-Synthese.

    Wichtig fuer die Ground Truth: starke, stabile Spektrallinien. Ein perkussiver
    "Pad" (schnelles Decay) verschmiert im Spektrum zu Breitbandrauschen und macht
    die Tonart unpruefbar.
    """
    tbl_size = 8192
    tbl = np.sin(2 * np.pi * np.arange(tbl_size) / tbl_size)
    x = np.zeros(n, dtype=np.float64)
    for f in freqs:
        for h in range(1, harmonics + 1):
            if f * h > 8000.0:
                break
            inc = tbl_size * f * h / SR
            ph = (np.arange(n) * inc) % tbl_size
            i0 = ph.astype(np.int64)
            frac = (ph - i0).astype(np.float32)
            x += (1.0 / h) * ((1 - frac) * tbl[i0] + frac * tbl[(i0 + 1) % tbl_size])
    x /= max(1.0, len(freqs) * 1.6)
    x = np.tanh(1.4 * x)
    # Einpol-Lowpass (vektorisiert ueber lfilter)
    from scipy.signal import lfilter
    a = float(np.exp(-2 * np.pi * cutoff / SR))
    y = lfilter([1 - a], [1, -a], x)
    # langsame Ein-/Ausblendung, kein perkussives Decay
    env = np.ones(n)
    fade = min(n, int(SR * 0.5))
    env[:fade] = np.linspace(0, 1, fade)
    env[-fade:] = np.linspace(1, 0, fade)
    return (y * env).astype(np.float32) * 0.5 * gain


def build(case: dict, seconds: float = 75.0) -> tuple[np.ndarray, dict]:
    rng = np.random.default_rng(abs(hash(case["name"])) % (2 ** 31))
    bpm = case["bpm"]
    beat = 60.0 / bpm
    n = int(seconds * SR)
    buf = np.zeros(n, dtype=np.float32)          # Harmonik + Bass
    drums = np.zeros(n, dtype=np.float32)        # Kick/Hats/Claps getrennt

    onset = case.get("onset", 0.0)
    pad_only = case.get("pad_intro", False)
    kick_on_2_4 = case.get("backbeat", False)

    # --- Harmonie -------------------------------------------------------
    root = case["root"]
    mode = case["mode"]  # 0 moll, 1 dur
    prog = MINOR_PROG if mode == 0 else MAJOR_PROG
    bar = beat * 4
    n_chord = len(prog)
    i = 0
    while i * bar < seconds:
        t0 = i * bar
        # kein Ueberlappen: ueberlappende Pad-Tails erzeugen Zusatz-Pitch-Classes
        # und machen die Tonart kuenstlich mehrdeutig
        n_samp = min(int(bar * SR), n - int(t0 * SR))
        start = int(t0 * SR)
        if start >= n or n_samp <= 0:
            break
        deg = prog[i % n_chord]
        pcs = [root, (root + 3) % 12, (root + 7) % 12] if mode == 0 else \
              [root, (root + 4) % 12, (root + 7) % 12]
        pcs = [(p + deg) % 12 for p in pcs]
        # Zwei Oktavlagen -> mehr spektrale Stuetze, weniger Oktav-Mehrdeutigkeit
        freqs = [hz(48 + p) for p in pcs] + [hz(60 + p) for p in pcs]
        buf[start:start + n_samp] += _pad_chord(n_samp, freqs, gain=1.0)
        i += 1

    # Intro ohne Beat fuer Offset-Faelle: Pad bleibt, Drums setzen spaeter ein
    # --- Kick -----------------------------------------------------------
    kick = _kick(int(0.28 * SR), rng)
    k = 0
    while True:
        t0 = onset + k * beat
        if t0 >= seconds:
            break
        beat_idx = k % 4
        if kick_on_2_4 and beat_idx not in (0, 2):
            k += 1
            continue
        s = int(t0 * SR)
        e = min(n, s + kick.size)
        drums[s:e] += kick[: e - s]
        k += 1

    # --- Offbeat-Hats ---------------------------------------------------
    hat = _hat(int(0.09 * SR), rng)
    k = 0
    while True:
        t0 = onset + (k + 0.5) * beat
        if t0 >= seconds:
            break
        s = int(t0 * SR)
        e = min(n, s + hat.size)
        drums[s:e] += hat[: e - s] * 0.8
        k += 1

    # --- Claps ----------------------------------------------------------
    if case.get("claps", case.get("backbeat", False)):
        clap = _clap(int(0.25 * SR), rng)
        k = 2
        while True:
            t0 = onset + k * beat
            if t0 >= seconds:
                break
            s = int(t0 * SR)
            e = min(n, s + clap.size)
            drums[s:e] += clap[: e - s] * 0.9
            k += 4

    # --- Bass (Achtel) --------------------------------------------------
    k = 0
    while True:
        t0 = onset + k * beat
        if t0 >= seconds:
            break
        f0 = hz(36 + root)
        b = _bass(int(beat * 0.45 * SR), f0)
        s = int(t0 * SR)
        e = min(n, s + b.size)
        buf[s:e] += b[: e - s] * 0.8
        k += 1

    # --- Rauschen / Textur ---------------------------------------------
    buf += (rng.standard_normal(n) * 0.010).astype(np.float32)

    # Pegel: Pad auf Club-Niveau, Drums darueber. Bewusst NICHT gemeinsam
    # normalisieren - sonst bestimmt die Kick den Pegel und das Pad verschwindet.
    pad_peak = float(np.max(np.abs(buf))) or 1.0
    buf = (buf / pad_peak * 0.28).astype(np.float32)
    drums = (drums / (float(np.max(np.abs(drums))) or 1.0) * 0.85).astype(np.float32)

    only = case.get("_only", "mix")
    if only == "pad":
        mix = buf / (float(np.max(np.abs(buf))) or 1.0) * 0.9
    elif only == "drums":
        mix = drums / (float(np.max(np.abs(drums))) or 1.0) * 0.9
    else:
        mix = buf + drums
        mix = mix / (float(np.max(np.abs(mix))) or 1.0) * 0.9
    mix = mix.astype(np.float32)

    truth = {
        "name": case["name"],
        "bpm": bpm,
        "key_camelot": CAMELOT[(root, mode)],
        "key_name": (NAMES_MAJOR if mode else NAMES_MINOR)[root],
        "note": case.get("note", ""),
    }
    return mix, truth


CASES: list[dict] = [
    # --- 12 Tonarten x beide Modi (gleiches Tempo, damit Key isoliert gemessen wird)
    *[dict(name=f"k_{NAMES_MINOR[pc]}_155", bpm=155, root=pc, mode=0) for pc in range(12)],
    *[dict(name=f"K_{NAMES_MAJOR[pc]}_155", bpm=155, root=pc, mode=1) for pc in range(12)],
    # --- Tempo-/Struktur-Haertefaelle
    dict(name="fast_gabber_178", bpm=178, root=2, mode=0, note="schnelles Tempo"),
    dict(name="slow_intro_128", bpm=128, root=9, mode=0, note="unterhalb des Suchfensters"),
    dict(name="pad_intro_60s_158", bpm=158, root=5, mode=0, onset=60.0,
         pad_intro=True, note="Beat startet erst nach 60 s (langer Ambient-Intro)"),
    dict(name="pad_intro_20s_155", bpm=155, root=0, mode=0, onset=20.0, pad_intro=True,
         note="Beat startet nach 20 s"),
    dict(name="backbeat_158", bpm=158, root=5, mode=0, backbeat=True, claps=True,
         note="Snare/Clap auf 2 und 4, Kick nur auf 1 und 3"),
]


def _write_variants(outdir: Path, name: str, case: dict) -> None:
    """Mischung / nur Pad / nur Drums als getrennte Dateien (Key-Robustheit)."""
    for mode_key, sub in (("mix", None), ("pad", "padonly"), ("drums", "drumsonly")):
        c = dict(case)
        c["_only"] = mode_key
        audio, _ = build(c)
        d = outdir if sub is None else outdir / sub
        d.mkdir(parents=True, exist_ok=True)
        raw = d / f"{name}.f32"
        raw.write_bytes(audio.tobytes())
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(SR),
                        "-ac", "1", "-i", str(raw), str(d / f"{name}.wav")], check=True)
        raw.unlink()


def main() -> None:
    outdir = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "corpus")
    outdir.mkdir(parents=True, exist_ok=True)
    truth_all = []
    for case in CASES:
        _write_variants(outdir, case["name"], case)
        _, truth = build(case)
        truth["path"] = str(outdir / f"{case['name']}.wav")
        truth_all.append(truth)
        print(f"{case['name']:24s} {truth['bpm']:5.0f} BPM  {truth['key_camelot']:>3s} "
              f"({truth['key_name']})", flush=True)
    (outdir / "truth.json").write_text(json.dumps(truth_all, indent=2), encoding="utf-8")
    print(f"\n{len(truth_all)} Cases -> {outdir}")


if __name__ == "__main__":
    main()
