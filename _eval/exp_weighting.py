"""A/B-Test: Welche Frame-Gewichtung haelt die Tonart richtig, wenn Drums dominieren?

Aufruf:  python _eval/exp_weighting.py [mix|padonly|drumsonly]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL))
import analysis_v2 as A  # noqa: E402

TRUTH = json.loads((EVAL / "corpus" / "truth.json").read_text(encoding="utf-8"))
MAJOR, MINOR = A.PROFILES["kk"]
N_FFT, HOP = 8192, 2048


def band_energy(y: np.ndarray, n_frames: int, sr: int = A.SR_CHROMA,
                n_fft: int = N_FFT, hop: int = HOP,
                band: tuple[float, float] = (100.0, 4000.0)) -> np.ndarray:
    """Energie pro Frame NUR im Harmonik-Band (Kick/Sub bleibt aussen vor)."""
    spec = A.stft_mag(y, n_fft, hop)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    m = (freqs >= band[0]) & (freqs <= band[1])
    e = spec[m].sum(axis=0)
    if e.size < n_frames:
        e = np.pad(e, (0, n_frames - e.size), mode="edge")
    return e[:n_frames]


def best_key(mean: np.ndarray) -> str | None:
    cm = mean - mean.mean()
    best = (-2.0, None)
    for pc in range(12):
        rot = np.roll(cm, -pc)
        for mode, prof in ((1, MAJOR), (0, MINOR)):
            p = prof - prof.mean()
            r = float(np.dot(rot, p) / (np.linalg.norm(rot) * np.linalg.norm(p) + 1e-12))
            if r > best[0]:
                best = (r, A.CAMELOT[(pc, mode)])
    return best[1]


def main() -> None:
    variant = sys.argv[1] if len(sys.argv) > 1 else "mix"
    sub = EVAL / "corpus" / (variant if variant != "mix" else "")

    names = ["rms_volle_bandbreite", "rms_100-4000Hz", "rms_hoehen_2-8kHz",
             "gleichgewichtung", "chroma_peakiness", "energie_x_peakiness"]
    hits = {k: 0 for k in names}
    soft = {k: 0.0 for k in names}
    n = 0

    for item in TRUTH:
        p = sub / f"{item['name']}.wav"
        y = A.decode(p)
        if y is None:
            continue
        y_c = A.resample(y, A.SR, A.SR_CHROMA)
        ch, w_full = A.chroma(y_c, sr=A.SR_CHROMA)
        if ch.size == 0:
            continue
        n += 1
        nf = ch.shape[1]
        e_band = band_energy(y_c, nf)
        e_high = band_energy(y_c, nf, band=(2000.0, 8000.0))
        e_band = e_band / (e_band.max() + 1e-12)
        e_high = e_high / (e_high.max() + 1e-12)
        srt = np.sort(ch, axis=0)
        peak = (srt[-1] - srt[-2])

        weights = {
            "rms_volle_bandbreite": w_full,
            "rms_100-4000Hz": e_band,
            "rms_hoehen_2-8kHz": e_high,
            "gleichgewichtung": np.ones(nf),
            "chroma_peakiness": peak,
            "energie_x_peakiness": e_band * peak,
        }
        for name, w in weights.items():
            w = np.asarray(w, dtype=np.float64)
            if not np.isfinite(w).all() or w.sum() <= 0:
                continue
            got = best_key(np.average(ch, axis=1, weights=w))
            if got == item["key_camelot"]:
                hits[name] += 1
            # weiche Bewertung: zaehlt auch den relativen Dur/Moll-Partner als halb richtig
            if got == item["key_camelot"]:
                soft[name] += 1.0
            elif got and got[:-1] == item["key_camelot"][:-1]:
                soft[name] += 0.5

    print(f"\nKorpus '{variant}', {n} Faelle")
    print(f"{'Gewichtung':26s}{'exakt':>10s}{'exakt+rel':>12s}")
    for name in sorted(names, key=lambda k: -hits[k]):
        print(f"{name:26s}{hits[name]:>6d}/{n:<3d}{100*soft[name]/max(n,1):>11.1f} %")


if __name__ == "__main__":
    main()
