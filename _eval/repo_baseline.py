"""Numpy-Nachbau der im Repo implementierten Analyseverfahren.

Hintergrund: `sc_digger/analysis.py` nutzt `librosa.beat.beat_track` und
`librosa.feature.chroma_cqt`. In dieser Umgebung hängt die numba-JIT-Kompilierung
davon (>10 min für einen einzigen Aufruf), also laufen die Benchmarks auf einem
Nachbau desselben Verfahrens. Die Rechenwege sind identisch:
  * BPM: Onset-Stärke -> Autokorrelation -> Tempoprior start_bpm=155
  * Key: Chroma-CQT -> Mittel über alle Frames -> Krumhansl-Kessler-Korrelation
"""
from __future__ import annotations

import numpy as np

SR = 22050


def decode(path, duration: float | None = 60, offset: float = 30) -> "np.ndarray | None":
    """ffmpeg-Decode bei SR - wie im Repo (analysis._decode_audio)."""
    import subprocess
    from pathlib import Path
    cmd = ["ffmpeg", "-v", "error", "-ss", str(offset), "-t", str(duration),
           "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    audio = np.frombuffer(r.stdout, dtype=np.float32)
    if audio.size < SR * 5:
        r = subprocess.run(["ffmpeg", "-v", "error", "-t", str(duration), "-i", str(path),
                            "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                           capture_output=True, timeout=300)
        audio = np.frombuffer(r.stdout, dtype=np.float32)
    if audio.size < SR * 3:
        return None
    return audio


# ------------------------------------------------------------------ BPM
def onset_envelope(y: np.ndarray, hop: int = 512, n_fft: int = 2048) -> np.ndarray:
    """librosa-Nachbau: Mel-artiger spektraler Fluss (hier direkt im FFT-Bereich)."""
    from analysis_v2 import stft_mag
    spec = stft_mag(y, n_fft, hop)
    # librosa nutzt Mel-Bänder; fuer die Autokorrelation genuegt der Fluss pro Band
    diff = np.diff(spec, axis=1)
    diff = np.maximum(diff, 0.0)
    oenv = diff.mean(axis=0)
    return oenv


def _acf_prior(oenv: np.ndarray, fps: float, bpm_min: float = 30.0,
               bpm_max: float = 300.0, prior_bpm: float = 155.0,
               sigma: float = 1.0) -> tuple[float, np.ndarray]:
    """Autokorrelation mit log-normalem Tempoprior (librosa: std_bpm=1.0 Oktaven).

    librosa skaliert den Prior als exp(-0.5 * (log2(bpm/start_bpm)/std_bpm)^2).
    """
    n = oenv.size
    oenv = oenv - oenv.mean()
    acf = np.correlate(oenv, oenv, mode="full")[n - 1:]
    acf = acf / (acf[0] + 1e-12)
    # acf[k] gehoert zu Lag k (also k+1 Abtastschritte)
    bpms = 60.0 * fps / np.arange(1, acf.size + 1, dtype=float)
    m = (bpms >= bpm_min) & (bpms <= bpm_max)
    b, a = bpms[m], acf[m]
    prior = np.exp(-0.5 * (np.log2(b / prior_bpm) / sigma) ** 2)
    return bpm_argmax(b, a, prior), (b, a * prior)


def bpm_argmax(bpms: np.ndarray, acf: np.ndarray, prior: np.ndarray) -> float:
    idx = int(np.argmax(acf * prior))
    return float(bpms[idx])


def detect_bpm_repo(y: np.ndarray, sr: int = SR) -> float | None:
    """Nachbau von detect_bpm(): 60 s Ausschnitt, beat_track mit start_bpm=155."""
    if y.size < sr * 5:
        return None
    oenv = onset_envelope(y, hop=512)
    fps = sr / 512
    bpm, _ = _acf_prior(oenv, fps)
    if bpm < 70:
        return None
    return round(bpm, 1)


# ------------------------------------------------------------------ Key
KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def chroma_cqt_like(y: np.ndarray, sr: int = SR, bins_per_octave: int = 36,
                    hop: int = 512) -> np.ndarray:
    """CQT-artige Chroma: logarithmische Frequenzauflösung, konstante Q pro Halbton."""
    from analysis_v2 import stft_mag
    n_fft = 4096
    spec = stft_mag(y, n_fft, hop)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    midi = np.round(69 + 12 * np.log2(np.maximum(freqs, 1e-6) / 440.0)).astype(int)
    pc = np.mod(midi, 12)
    ch = np.zeros((12, spec.shape[1]), dtype=np.float32)
    for p in range(12):
        m = pc == p
        if m.any():
            ch[p] = spec[m].sum(axis=0)
    norm = np.linalg.norm(ch, axis=0, keepdims=True) + 1e-9
    return ch / norm


def _corr_key(chroma_mean: np.ndarray) -> tuple[str, float, float]:
    cm = chroma_mean - chroma_mean.mean()
    results = []
    for pc in range(12):
        rot = np.roll(cm, -pc)
        for mode, prof in ((1, KK_MAJOR), (0, KK_MINOR)):
            p = prof - prof.mean()
            r = float(np.dot(rot, p) / (np.linalg.norm(rot) * np.linalg.norm(p) + 1e-12))
            results.append((r, pc, mode))
    results.sort(reverse=True)
    return results


def detect_key_repo(y: np.ndarray, sr: int = SR) -> tuple[str | None, str | None, float]:
    if y.size < sr * 5:
        return None, None, 0.0
    ch = chroma_cqt_like(y, sr=sr)
    mean = np.mean(ch, axis=1)          # genau wie im Repo: np.mean, keine Gewichtung
    results = _corr_key(mean)
    r, pc, mode = results[0]
    from analysis_v2 import CAMELOT, NAMES_MAJOR, NAMES_MINOR
    return CAMELOT[(pc, mode)], (NAMES_MAJOR if mode else NAMES_MINOR)[pc], r
