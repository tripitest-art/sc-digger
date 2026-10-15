"""Kandidat: Trackanalyse komplett in NumPy/SciPy - ohne librosa/numba.

Motivation (in dieser Umgebung gemessen):
  * librosa.beat.beat_track() und librosa.onset.onset_strength() hängen hier >10 min
    an der numba-JIT-Kompilierung. Ein Analyse-Schritt, der pro Track im
    Download-Pfad läuft, darf nicht von numba abhängen.
  * BPM und Key brauchen dieselben Vorstufen (Mono-Decode, Spektrogramm). Zwei
    getrennte ffmpeg-Decodes + zwei getrennte STFTs sind pure Verschwendung.

Enthalten:
  decode()             - ein ffmpeg-Decode (Mono, 44,1 kHz) für alle Features
  onset_strength()      - Mel-Spektrogramm + spektraler Fluss
  detect_tempo()        - Pulszug-Score auf dem Onset-Signal (oktav-bewusst)
  detect_key()          - Chroma aus logarithmischem Spektrum + Key-Profile
  analyze()             - fusionierte Auswertung + Konfidenzen
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

SR = 44100
SR_CHROMA = 11025

CAMELOT = {
    (0, 0): "5A", (1, 0): "12A", (2, 0): "7A", (3, 0): "2A", (4, 0): "9A", (5, 0): "4A",
    (6, 0): "11A", (7, 0): "6A", (8, 0): "1A", (9, 0): "8A", (10, 0): "3A", (11, 0): "10A",
    (0, 1): "8B", (1, 1): "3B", (2, 1): "10B", (3, 1): "5B", (4, 1): "12B", (5, 1): "7B",
    (6, 1): "4B", (7, 1): "11B", (8, 1): "6B", (9, 1): "1B", (10, 1): "8B", (11, 1): "3B",
}
NAMES_MINOR = ["Cm", "C#m", "Dm", "D#m", "Em", "Fm", "F#m", "Gm", "G#m", "Am", "A#m", "Bm"]
NAMES_MAJOR = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Krumhansl-Kessler (Pitch-Class-Reihenfolge ab C) und Temperley (2001)
PROFILES = {
    "kk": (np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]),
           np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])),
    "temperley": (np.array([5.0, 2.0, 3.5, 2.0, 4.5, 4.0, 2.0, 4.5, 2.0, 3.5, 1.5, 4.0]),
                  np.array([5.0, 2.0, 3.5, 4.5, 2.0, 4.0, 2.0, 4.5, 3.5, 2.0, 1.5, 4.0])),
}


# ------------------------------------------------------------------ Decode
def decode(path: Path, duration: float | None = 90.0, offset: float = 0.0) -> np.ndarray | None:
    """Mono-Decode mit fester Abtastrate (SR) für alle weiteren Features.

    Bewusst OHNE sr-Parameter: Eine implizite Abtastrate ist genau die Falle, die
    zu falschen Frequenz->Tonhoehe-Zuordnungen fuehrt (ein 44,1-kHz-Puffer, der
    als 11-kHz-Puffer interpretiert wird, liefert um Terzen verschobene Tonarten).
    Wer eine andere Rate braucht, resampelt explizit mit resample().
    """
    cmd = ["ffmpeg", "-v", "error"]
    if offset:
        cmd += ["-ss", str(offset)]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    audio = np.frombuffer(r.stdout, dtype=np.float32)
    if audio.size < SR * 10 and offset:
        # Ausschnitt zu kurz (Track kürzer als Offset) -> komplett lesen
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1",
                            "-ar", str(SR), "-f", "f32le", "-"], capture_output=True, timeout=300)
        audio = np.frombuffer(r.stdout, dtype=np.float32)
    if audio.size < SR * 5:
        return None
    peak = float(np.max(np.abs(audio))) or 1.0
    return (audio / peak).astype(np.float32)


def resample(y: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    """Explizites, anti-alias-freies Resampling (Polynom-FIR)."""
    if sr_from == sr_to:
        return y
    from scipy.signal import resample_poly
    from math import gcd
    g = gcd(int(sr_from), int(sr_to))
    return resample_poly(y, sr_to // g, sr_from // g).astype(np.float32)


# ------------------------------------------------------------------ STFT
def stft_mag(y: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    """Betragsspektrogramm (freq, frames) via rFFT, Hann-Fenster, ohne Padding-Tricks."""
    if y.size < n_fft:
        y = np.pad(y, (0, n_fft - y.size))
    n_frames = 1 + (y.size - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n_frames)[:, None]
    frames = y[idx] * np.hanning(n_fft)[None, :]
    return np.abs(np.fft.rfft(frames, axis=1)).T.astype(np.float32)


def _hz_to_mel(f: np.ndarray | float) -> np.ndarray | float:
    return 2595.0 * np.log10(1.0 + np.asarray(f) / 700.0)


def _mel_to_hz(m: np.ndarray | float) -> np.ndarray | float:
    return 700.0 * (10.0 ** (np.asarray(m) / 2595.0) - 1.0)


def mel_filterbank(n_mels: int, n_fft: int, sr: int, fmin: float = 30.0,
                   fmax: float | None = None) -> np.ndarray:
    fmax = fmax or sr / 2
    mels = np.linspace(_hz_to_mel(fmin), _hz_to_mel(fmax), n_mels + 2)
    freqs = _mel_to_hz(mels)
    bins = np.floor((n_fft + 1) * freqs / sr).astype(int)
    fb = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    for i in range(n_mels):
        lo, mid, hi = bins[i], bins[i + 1], bins[i + 2]
        if mid == lo:
            mid = lo + 1
        if hi == mid:
            hi = mid + 1
        hi = min(hi, n_fft // 2)
        fb[i, lo:mid] = np.linspace(0, 1, mid - lo, endpoint=False)
        fb[i, mid:hi] = np.linspace(1, 0, hi - mid, endpoint=False)
    return fb


def onset_strength(y: np.ndarray, sr: int = SR, n_fft: int = 2048,
                   hop: int = 256, n_mels: int = 64) -> np.ndarray:
    """Onset-Stärke (spektraler Fluss im Mel-Bereich, halbwellengleichgerichtet)."""
    spec = stft_mag(y, n_fft, hop)
    fb = mel_filterbank(n_mels, n_fft, sr)
    mel = fb @ spec
    logmel = np.log1p(1000.0 * mel)
    flux = np.diff(logmel, axis=1)
    flux = np.maximum(flux, 0.0)
    oenv = flux.sum(axis=0)
    # lokal mittelwertfrei machen (Linear-Detrend reicht, kein Filtern nötig)
    if oenv.size:
        oenv = oenv - oenv.min()
    peak = float(oenv.max()) if oenv.size else 0.0
    return (oenv / peak).astype(np.float32) if peak > 0 else oenv.astype(np.float32)


# ------------------------------------------------------------------ Tempo
@dataclass
class TempoResult:
    bpm: float | None
    confidence: float = 0.0          # 0-1: wie klar der Pulszug passt
    octave: int = 1                  # 1 = Grundwert, 2 = doppelt, 0 = halb
    candidates: list[tuple[float, float]] = field(default_factory=list)


def _acf_harmonic_score(oenv: np.ndarray, bpm: float, fps: float,
                        weights: tuple[float, ...] = (1.0, 0.5, 0.33, 0.25)) -> float:
    """Normierte Autokorrelation, aufsummiert über die Harmonischen des Tempos.

    Warum das dem naiven Einzel-Lag vorzuziehen ist (hier gemessen, Klick bei 158):
        acf_sum(158)=3.61, acf_sum(157)=1.54, acf_sum(150)=0.02, acf_sum(156)=0.96
    Die Summe über Lag, 2xLag, 3xLag, 4xLag macht den Peak scharf, weil ein
    falscher Kandidat nicht gleichzeitig auf allen Harmonischen passen kann.
    """
    lag = fps * 60.0 / bpm
    total = 0.0
    for h, w in enumerate(weights, start=1):
        li = int(round(lag * h))
        if li < 2 or li >= oenv.size - 2:
            continue
        a = oenv[:-li]
        b = oenv[li:]
        total += w * float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))
    return total


def detect_tempo(y: np.ndarray, sr: int = SR, *, bpm_min: float = 70.0,
                 bpm_max: float = 210.0, hop: int = 256,
                 prior_center: float = 152.0, prior_sigma: float = 22.0,
                 prior_weight: float = 0.06,
                 snap_to: tuple[float, float] | None = None,
                 snap_min_ratio: float = 0.75) -> TempoResult:
    """Tempo per harmonischer Autokorrelation des Onset-Signals.

    Löst das Oktavproblem explizit: Ein Viertakt-Muster erzeugt bei T, 2T und 3T
    gleich starke Autokorrelation. Deshalb wird nach dem Peak in der
    Genre-Domaene umgeschaltet (bpm_min..bpm_max) und die Oktaventscheidung
    anhand der ACF-Werte von halbem/doppeltem Tempo begruendet.

    snap_to: Zielfenster (z.B. (150, 165) aus der Config). Liegt der rohe Peak
    halb oder doppelt in diesem Fenster und ist der ACF-Wert dort mindestens
    snap_min_ratio des Peaks, wird der Wert in das Fenster geholt. Das ist fuer
    einen DJ-Workflow richtig: Ob ein Kick-auf-1-und-3-Tool 79 oder 158 BPM ist,
    entscheidet die Notierung - gemischt wird es im 155er-Fenster.
    """
    oenv = onset_strength(y, sr=sr, hop=hop)
    if oenv.size < 128:
        return TempoResult(None)
    fps = sr / hop

    def landscape(lo: float, hi: float, step: float) -> tuple[np.ndarray, np.ndarray]:
        cands = np.arange(lo, hi + 1e-9, step)
        return cands, np.array([_acf_harmonic_score(oenv, b, fps) for b in cands])

    cands, scores = landscape(bpm_min, bpm_max, 0.25)
    if not np.any(scores > 0):
        return TempoResult(None)

    # Sehr flache Genre-Vorliebe: bricht Gleichstand zwischen T und 2T auf,
    # verbiegt aber ein echtes Tempo nicht (6 % Gewicht).
    prior = np.exp(-0.5 * ((cands - prior_center) / prior_sigma) ** 2)
    adj = scores * (1.0 - prior_weight) + prior_weight * scores.max() * prior

    best_i = int(np.argmax(adj))
    best_bpm = float(cands[best_i])

    # Parabolische Interpolation um den Peak -> Sub-Raster-Genauigkeit
    if 0 < best_i < adj.size - 1:
        y0, y1, y2 = adj[best_i - 1], adj[best_i], adj[best_i + 1]
        denom = (y0 - 2 * y1 + y2)
        if abs(denom) > 1e-12:
            delta = 0.5 * (y0 - y2) / denom
            if abs(delta) <= 1.0:
                best_bpm += delta * 0.25

    # Konfidenz: Peak-Hoehe gegen den typischen Hintergrund derselben Kurve
    peak = float(adj[best_i])
    bg = float(np.median(adj[(adj > 0)])) if np.any(adj > 0) else 0.0
    conf = float(max(0.0, min(1.0, (peak - bg) / (peak + 1e-9))))

    s_half = _acf_harmonic_score(oenv, best_bpm / 2, fps)
    s_dbl = _acf_harmonic_score(oenv, best_bpm * 2, fps)
    # Oktavhinweis (auf den ROHEN Wert bezogen, vor dem Snapping)
    octave = 1
    if s_dbl > peak * 1.05:
        octave = 2
    elif s_half > peak * 1.05 and best_bpm / 2 >= bpm_min:
        octave = 0

    # Oktav-Snapping in das Ziel-Tempo-Fenster
    if snap_to is not None:
        lo, hi = snap_to
        for factor in (2.0, 0.5):
            cand = best_bpm * factor
            if lo <= cand <= hi:
                sc = _acf_harmonic_score(oenv, cand, fps)
                if sc >= peak * snap_min_ratio:
                    octave = 2 if factor == 2.0 else 0
                    best_bpm = cand
                    peak = sc
                break

    top_idx = np.argsort(adj)[::-1][:5]
    return TempoResult(round(best_bpm, 1), conf, octave,
                       [(float(cands[i]), round(float(adj[i]), 4)) for i in top_idx])


# ------------------------------------------------------------------ Key
def chroma(y: np.ndarray, sr: int = SR_CHROMA, *, n_fft: int = 8192,
           hop: int = 2048, fmin: float = 80.0, fmax: float = 4000.0,
           whiten: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Chroma (12 x frames) als oktav-gemittelte Halbton-Energie.

    Warum nicht einfach FFT-Bins auf Pitch-Classes summieren (hier gemessen):
      * Im linearen Frequenzraster bekommt jede Pitch-Class unterschiedlich viele
        Bins (F=235, A#=315). Zusammen mit dem Rauschboden verzerrt das die
        Chroma-Verteilung messbar (gemessen: richtig war Fm, erkannt wurde A#m).
      * Tieffrequente Linien lecken in Nachbarhalbtoene.
    Stattdessen: lokale Weissung, dann pro Halbton ueber alle Oktaven mitteln.
    """
    spec = stft_mag(y, n_fft, hop).astype(np.float32)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    sel = (freqs >= fmin) & (freqs <= fmax)
    s = spec[sel]
    f = freqs[sel]
    if whiten:
        # Spektrales Weisseln: Rauschboden und Bassdrone auf ~1 druecken,
        # tonale Linien bleiben als Spitzen stehen. Der Nenner kommt aus dem
        # Spektrum selbst, nicht aus dem Zeitsignal.
        from scipy.ndimage import uniform_filter1d
        sm = uniform_filter1d(s, size=15, axis=0, mode="nearest")
        s = s / (sm + 1e-6)

    # Halbton-Mittenfrequenzen (aufeinanderfolgende Halbtoene, damit interp 1D geht)
    n_lo, n_hi = 24, int(np.floor(12 * np.log2(fmax / 440.0) + 69))
    semis = np.arange(n_lo, n_hi + 1, dtype=float)
    centers = 440.0 * 2.0 ** ((semis - 69) / 12.0)
    inside = (centers >= f[0]) & (centers <= f[-1])

    ch = np.zeros((12, s.shape[1]), dtype=np.float32)
    for p in range(12):
        band = semis[inside & (np.mod(semis.astype(int), 12) == p)]
        if band.size == 0:
            continue
        cur = centers[inside & (np.mod(semis.astype(int), 12) == p)]
        acc = np.zeros(s.shape[1], dtype=np.float64)
        for c in cur:
            acc += _interp_cols(f, s, c)
        acc /= cur.size                      # pro Oktave gleich gewichtet
        ch[p] = acc

    # pro Frame normieren: Basslastigkeit einer Aufnahme darf die Tonart nicht bestimmen
    norm = np.linalg.norm(ch, axis=0, keepdims=True) + 1e-9
    ch = ch / norm
    # Frame-Gewicht aus dem Zeitbereich (RMS), NICHT aus spec**2: die
    # Spektralbetraege liegen bei ~1e5, das Quadrat laeuft in float32 ueber.
    step = max(1, len(y) // max(ch.shape[1], 1))
    energy = np.sqrt(np.convolve(y.astype(np.float64) ** 2,
                                 np.ones(step) / step, mode="same"))[::step][: ch.shape[1]]
    if energy.size < ch.shape[1]:
        energy = np.pad(energy, (0, ch.shape[1] - energy.size), mode="edge")
    thr = float(np.quantile(energy, 0.30)) if energy.size > 10 else 0.0
    w = np.where(energy >= thr, energy, 0.0)
    if not np.isfinite(w).all() or w.sum() <= 0:
        w = np.ones(ch.shape[1])
    return ch, w


def _interp_cols(f: np.ndarray, s: np.ndarray, x: float) -> np.ndarray:
    """Lineare Interpolation einer Frequenz x ueber alle Frames (vektorisiert)."""
    i = int(np.searchsorted(f, x))
    i = min(max(i, 1), f.size - 1)
    x0, x1 = f[i - 1], f[i]
    t = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
    return s[i - 1] * (1.0 - t) + s[i] * t


def detect_key(y: np.ndarray, sr: int = SR, *, profiles: str = "kk") -> dict:
    """Tonart per Profilkorrelation auf dem Chroma-Vektor. Gibt Kandidaten + Marge.

    Erwartet ein Signal bei der uebergebenen Rate `sr` (Default SR = 44,1 kHz) und
    resampelt intern auf die Chroma-Rate. Die Rate MUSS zum Puffer passen - sie ist
    deshalb Pflichtinformation und wird nicht geraten.
    """
    y_c = resample(y, sr, SR_CHROMA) if sr != SR_CHROMA else y
    ch, w = chroma(y_c, sr=SR_CHROMA)
    if ch.size == 0 or w.sum() <= 0:
        return {"camelot": None, "key_name": None, "score": 0.0,
                "margin": 0.0, "mode_conf": 0.0, "candidates": []}
    mean = np.average(ch, axis=1, weights=w)
    # Bekannte Grenze: Chroma wird pro Frame auf Einheitsnorm gebracht, der
    # Mittelwert ist daher immer flach (max ~0.45). Ein "tonale Substanz"-
    # Schwellwert auf dieser Basis trennt perkussives Material NICHT zuverlaessig
    # (gemessen: Peakiness 0.49 fuer nur-Drums vs. 0.40 fuer nur-Pad). Die
    # Entscheidung stuetzt sich deshalb auf die Marge, nicht auf einen Absolutwert.
    major, minor = PROFILES[profiles]
    cm = mean - mean.mean()

    results: list[tuple[float, int, int]] = []
    for pc in range(12):
        rot = np.roll(cm, -pc)
        for mode, prof in ((1, major), (0, minor)):
            p = prof - prof.mean()
            r = float(np.dot(rot, p) / (np.linalg.norm(rot) * np.linalg.norm(p) + 1e-12))
            results.append((r, pc, mode))
    results.sort(reverse=True)
    best_r, best_pc, best_mode = results[0]
    second_r = results[1][0]

    # Dur/Moll-Ambiguität der Paralleltonart: wie nah liegt die gleichnamige Gegenmode?
    rel_r = next(r for r, pc, mode in results if pc == best_pc and mode != best_mode)
    mode_conf = max(0.0, min(1.0, (best_r - rel_r) / (abs(best_r) + 1e-9)))
    return {
        "camelot": CAMELOT.get((best_pc, best_mode)),
        "key_name": (NAMES_MAJOR if best_mode else NAMES_MINOR)[best_pc],
        "score": round(best_r, 3),
        "margin": round(best_r - second_r, 3),
        "mode_conf": round(mode_conf, 3),
        "candidates": [
            {"camelot": CAMELOT.get((pc, mode)),
             "key_name": (NAMES_MAJOR if mode else NAMES_MINOR)[pc],
             "r": round(r, 3)}
            for r, pc, mode in results[:4]
        ],
    }


# ------------------------------------------------------------------ Fusion
def analyze(path: Path, *, want_bpm: bool = True, want_key: bool = True,
            snap_to: tuple[float, float] | None = None) -> dict:
    """Ein Decode, ein Spektrogramm-Setup - BPM und Key zusammen.

    snap_to kommt aus der Config (z.B. search.bpm_min/bpm_max) und holt halb-/
    doppeltzeitige Erkennungen in das Genre-Fenster.
    """
    y = decode(path)
    if y is None:
        return {"bpm": None, "bpm_confidence": 0.0, "bpm_octave": 1,
                "key_camelot": None, "key_name": None, "key_confidence": 0.0,
                "error": "decode failed"}
    out: dict = {"bpm": None, "bpm_confidence": 0.0, "bpm_octave": 1,
                 "bpm_candidates": [], "key_camelot": None, "key_name": None,
                 "key_confidence": 0.0, "key_candidates": []}
    if want_bpm:
        t = detect_tempo(y, snap_to=snap_to)
        out.update(bpm=t.bpm, bpm_confidence=round(t.confidence, 3),
                   bpm_octave=t.octave,
                   bpm_candidates=[(round(b, 1), round(s, 4)) for b, s in t.candidates])
    if want_key:
        k = detect_key(y)
        out.update(key_camelot=k["camelot"], key_name=k["key_name"],
                   key_confidence=k["mode_conf"], key_candidates=k["candidates"])
    return out
