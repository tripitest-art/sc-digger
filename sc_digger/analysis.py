"""Audio-Analyse: BPM-Erkennung und Tonart-Erkennung (Camelot-Key).

BPM und Key kommen aus einem gemeinsamen Analysepfad in reinem NumPy/SciPy
(Mel-Fluss-Onsets, harmonische Autokorrelation, oktav-gemitteltes Chroma). Der
Pfad wurde in `_eval/analysis_v2.py` erprobt und in `_eval/REVIEW-2026-10-14.md`
(Abschnitte 1-5, 8.4, 10) nachgewiesen. Das Audio wird dafür genau einmal
dekodiert (Mono, 44,1 kHz) statt je einmal pro Kennwert.

Warum numba-frei: `librosa.beat.beat_track()` und `librosa.onset.onset_strength()`
hängen in der numba-JIT-Kompilierung auf manchen Systemen >10 Minuten. Ein
Schritt im Download-Pfad darf davon nicht abhängen.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# Abtastrate des gemeinsamen Decodes und Rate der Chroma-Berechnung. Die Rate
# gehört zum Puffer: Ein als 11 kHz gelesener 44,1-kHz-Puffer liefert um Terzen
# verschobene Tonarten. Deshalb kein impliziter sr-Vorgabewert im Analysepfad;
# andere Raten gibt es nur über resample().
SR = 44100
SR_CHROMA = 11025

# Camelot-Wheel: Zuordnung (pitch_class, mode) -> Camelot-Key.
# mode=1 (Dur), mode=0 (Moll).
# pitch_class: 0=C, 1=C#, 2=D, ... 11=B
_CAMELOT = {
    # Moll (A-Notation)
    (0, 0): "5A",   # C minor
    (1, 0): "12A",  # C# minor
    (2, 0): "7A",   # D minor
    (3, 0): "2A",   # D# / Eb minor
    (4, 0): "9A",   # E minor
    (5, 0): "4A",   # F minor
    (6, 0): "11A",  # F# minor
    (7, 0): "6A",   # G minor
    (8, 0): "1A",   # G# / Ab minor
    (9, 0): "8A",   # A minor
    (10, 0): "3A",  # A# / Bb minor
    (11, 0): "10A", # B minor
    # Dur (B-Notation): gleiche Zahl wie die Moll-Parallele (3 Halbtöne tiefer)
    (0, 1): "8B",   # C major
    (1, 1): "3B",   # C# major
    (2, 1): "10B",  # D major
    (3, 1): "5B",   # D# / Eb major
    (4, 1): "12B",  # E major
    (5, 1): "7B",   # F major
    (6, 1): "2B",   # F# major
    (7, 1): "9B",   # G major
    (8, 1): "4B",   # G# / Ab major
    (9, 1): "11B",  # A major
    (10, 1): "6B",  # A# / Bb major
    (11, 1): "1B",  # B major
}

# Pitch-Class -> Open-Key-Notation (Alternative zu Camelot, für ID3-Tags)
_KEY_NAMES_MINOR = ["Cm", "C#m", "Dm", "D#m", "Em", "Fm", "F#m", "Gm", "G#m", "Am", "A#m", "Bm"]
_KEY_NAMES_MAJOR = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Key-Profile in Pitch-Class-Reihenfolge ab C, jeweils (Dur, Moll).
_PROFILES = {
    # Krumhansl-Kessler (1990)
    "kk": (np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]),
           np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])),
    # Temperley (2001)
    "temperley": (np.array([5.0, 2.0, 3.5, 2.0, 4.5, 4.0, 2.0, 4.5, 2.0, 3.5, 1.5, 4.0]),
                  np.array([5.0, 2.0, 3.5, 4.5, 2.0, 4.0, 2.0, 4.5, 3.5, 2.0, 1.5, 4.0])),
}


def _decode_audio(path: Path, sr: int = 22050, duration: int = 60,
                   offset: int = 30) -> np.ndarray | None:
    """Dekodiert einen Ausschnitt via ffmpeg zu Mono-Float-PCM.

    Vermeidet die direkte Abhängigkeit von libsndfile/audioread, die auf manchen
    Systemen Probleme machen. ffmpeg ist ohnehin bereits für quality.py nötig.
    """
    cmd = [
        "ffmpeg", "-v", "error",
        "-ss", str(offset), "-t", str(duration),
        "-i", str(path),
        "-ac", "1", "-ar", str(sr), "-f", "f32le", "-",
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=180)
    audio = np.frombuffer(r.stdout, dtype=np.float32)
    if audio.size < sr * 5 and offset:
        # Offset war zu weit -> vom Anfang versuchen. Ohne Offset wäre der
        # zweite Aufruf identisch zum ersten und kostete nur I/O; der
        # Analysepfad dekodiert mit Offset 0 und braucht genau einen Aufruf.
        cmd_start = [
            "ffmpeg", "-v", "error",
            "-t", str(duration),
            "-i", str(path),
            "-ac", "1", "-ar", str(sr), "-f", "f32le", "-",
        ]
        r = subprocess.run(cmd_start, capture_output=True, timeout=180)
        audio = np.frombuffer(r.stdout, dtype=np.float32)
    if audio.size < sr * 3:
        return None
    return audio


# ----------------------------------------------------------- Decode-Vorstufe
def _decode_for_analysis(path: Path) -> np.ndarray | None:
    """Ein einziger ffmpeg-Decode (Mono, 44,1 kHz) als Vorstufe für BPM und Key."""
    audio = _decode_audio(path, sr=SR, duration=90, offset=0)
    if audio is None:
        return None
    peak = float(np.max(np.abs(audio))) or 1.0
    return (audio / peak).astype(np.float32)


def resample(y: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    """Explizites, anti-alias-freies Resampling (Polynom-FIR)."""
    if sr_from == sr_to:
        return y
    from math import gcd

    from scipy.signal import resample_poly

    g = gcd(int(sr_from), int(sr_to))
    return resample_poly(y, sr_to // g, sr_from // g).astype(np.float32)


# ------------------------------------------------------------- Spektrogramm
def stft_mag(y: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    """Betragsspektrogramm (Frequenz × Frames) mit Hann-Fenster."""
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
    """Dreieckige Mel-Filterbank (n_mels × n_fft/2+1)."""
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
    """Onset-Stärke: halbwellengleichgerichteter spektraler Fluss im Mel-Bereich.

    Der Mel-Fluss trägt die Verzerrung des linearen Frequenzrasters nicht mit:
    Jedes Band hat dieselbe Bandbreite, statt dass eine Pitch-Class (F=235)
    wesentlich mehr FFT-Bins bekommt als eine andere (A#=315).
    """
    spec = stft_mag(y, n_fft, hop)
    fb = mel_filterbank(n_mels, n_fft, sr)
    mel = fb @ spec
    flux = np.maximum(np.diff(np.log1p(1000.0 * mel), axis=1), 0.0)
    oenv = flux.sum(axis=0)
    if oenv.size:
        oenv = oenv - oenv.min()
    peak = float(oenv.max()) if oenv.size else 0.0
    return (oenv / peak).astype(np.float32) if peak > 0 else oenv.astype(np.float32)


# -------------------------------------------------------------------- Tempo
@dataclass
class TempoResult:
    bpm: float | None
    confidence: float = 0.0          # 0-1: wie klar der Pulszug passt
    octave: int = 1                  # 1 = Grundwert, 2 = verdoppelt, 0 = halbiert
    candidates: list[tuple[float, float]] = field(default_factory=list)


def _acf_harmonic_score(oenv: np.ndarray, bpm: float, fps: float,
                        weights: tuple[float, ...] = (1.0, 0.5, 0.33, 0.25)) -> float:
    """Normierte Autokorrelation, summiert über die Harmonischen des Tempos.

    Die Summe über Lag, 2×Lag, 3×Lag, 4×Lag macht den Peak scharf: Ein falscher
    Kandidat kann nicht gleichzeitig auf allen Harmonischen passen.
    """
    lag = fps * 60.0 / bpm
    total = 0.0
    for h, w in enumerate(weights, start=1):
        li = int(round(lag * h))
        if li < 2 or li >= oenv.size - 2:
            continue
        a, b = oenv[:-li], oenv[li:]
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
    gleich starke Autokorrelation. Deshalb wird der Peak in der Genre-Domäne
    gesucht und die Oktaventscheidung anhand der ACF-Werte von halbem/doppeltem
    Tempo begründet.

    snap_to: Zielfenster (z. B. (search.bpm_min, search.bpm_max)). Liegt der rohe
    Peak halb oder doppelt in diesem Fenster und ist der ACF-Wert dort mindestens
    snap_min_ratio des Peaks, wird der Wert in das Fenster geholt. Für einen
    DJ-Workflow ist das richtig: Ob ein Kick-auf-1-und-3-Tool 79 oder 158 BPM
    ist, entscheidet die Notierung – gemischt wird es im 155er-Fenster.
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

    # Konfidenz: Peak-Höhe gegen den typischen Hintergrund derselben Kurve
    peak = float(adj[best_i])
    bg = float(np.median(adj[adj > 0])) if np.any(adj > 0) else 0.0
    conf = float(max(0.0, min(1.0, (peak - bg) / (peak + 1e-9))))

    s_half = _acf_harmonic_score(oenv, best_bpm / 2, fps)
    s_dbl = _acf_harmonic_score(oenv, best_bpm * 2, fps)
    # Oktavhinweis, auf den rohen Wert vor dem Snapping bezogen
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
    # float(): die parabolische Interpolation macht aus dem Wert sonst ein
    # numpy-Skalar, der beim Serialisieren (JSON) auffällt.
    return TempoResult(float(round(best_bpm, 1)), conf, octave,
                       [(float(cands[i]), round(float(adj[i]), 4)) for i in top_idx])


def detect_bpm(path: Path, snap_to: tuple[float, float] | None = None) -> float | None:
    """Erkennt das Tempo (BPM) eines Audio-Files aus dem Audiomaterial.

    snap_to: Zielfenster (z. B. (search.bpm_min, search.bpm_max)); halb- oder
    doppeltzeitige Erkennungen rasten darin ein (siehe detect_tempo).

    Gibt den wahrscheinlichsten BPM-Wert zurück, oder None wenn die Erkennung
    fehlschlägt. Optimiert für den Bereich 70–210 BPM (Techno/Schranz).
    """
    try:
        audio = _decode_for_analysis(path)
        if audio is None:
            return None
        result = detect_tempo(audio, snap_to=snap_to)
        if result.bpm is None or result.bpm < 70:
            return None
        return float(result.bpm)
    except Exception as e:
        log.warning("BPM-Erkennung fehlgeschlagen für %s: %s", path.name, e)
        return None


def resolve_bpm(audio_bpm: float | None, text_bpm: float | None, *,
                window: tuple[float, float], plausible: tuple[float, float] = (120.0, 200.0),
                text_tolerance: float = 3.0, audio_confidence: float | None = None,
                min_confidence: float = 0.6) -> tuple[float | None, str]:
    """Korrigiert Oktavfehler der Audio-BPM (halbes/doppeltes Tempo).

    Onset-basierte Tempoerkennung rastet bei Halftime-Passagen oder starken Offbeats
    gern auf x/2 oder 2x ein. Aus den Kandidaten {x/2, x, 2x} wird gewählt:

    1. Nennt der Uploader eine BPM (text_bpm), gewinnt der Kandidat nahe daran.
       Liegt keiner innerhalb text_tolerance, wird der Textwert übernommen
       (Uploader-Angaben sind bei Techno meist korrekt, Audio-Erkennung irrt eher) –
       es sei denn, die Audio-Erkennung ist sicher: ab audio_confidence >= min_confidence
       schlägt sie die Uploader-Angabe (falsche Titel-BPM wie "150" bei echten 155) und
       durchläuft die Schritte 2 bis 4. Ohne audio_confidence (None) bleibt es beim Textwert.
    2. Sonst gewinnt der Kandidat im Suchfenster (z. B. 150–165).
    3. Sonst der Kandidat im plausiblen Genre-Bereich, der dem Fenster am nächsten liegt.
    4. Sonst bleibt der Audiowert unverändert.

    Gibt (bpm, grund) zurück; grund ist für Logs/Notizen gedacht.
    """
    if audio_bpm is None or audio_bpm <= 0:
        return (text_bpm, "nur Text-BPM") if text_bpm else (None, "keine BPM")

    candidates = [audio_bpm / 2, audio_bpm, audio_bpm * 2]

    def label(c: float) -> str:
        if c == audio_bpm:
            return "Audio"
        return "Audio ×2" if c > audio_bpm else "Audio ÷2"

    if text_bpm:
        best = min(candidates, key=lambda c: abs(c - text_bpm))
        if abs(best - text_bpm) <= text_tolerance:
            return round(best, 1), f"{label(best)}, bestätigt durch Text-BPM {text_bpm:.0f}"
        if audio_confidence is None or audio_confidence < min_confidence:
            return float(text_bpm), f"Text-BPM {text_bpm:.0f} statt Audio {audio_bpm:.1f} (kein Oktavbezug)"
        bpm, why = resolve_bpm(audio_bpm, None, window=window, plausible=plausible)
        return bpm, f"{why}; Text-BPM {text_bpm:.0f} überstimmt (Audio-Konfidenz {audio_confidence:.2f})"

    lo, hi = window
    in_window = [c for c in candidates if lo <= c <= hi]
    if in_window:
        c = in_window[0]
        return round(c, 1), label(c) + " (im Suchfenster)"

    p_lo, p_hi = plausible
    center = (lo + hi) / 2
    in_range = [c for c in candidates if p_lo <= c <= p_hi]
    if in_range:
        c = min(in_range, key=lambda c: abs(c - center))
        return round(c, 1), label(c) + " (plausibler Bereich)"

    return round(audio_bpm, 1), "Audio (unkorrigiert, außerhalb plausibler Bereiche)"


# ---------------------------------------------------------------------- Key
def chroma(y: np.ndarray, sr: int = SR_CHROMA, *, n_fft: int = 8192,
           hop: int = 2048, fmin: float = 80.0, fmax: float = 4000.0,
           whiten: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Chroma (12 × Frames) als oktav-gemittelte Halbton-Energie, plus Frame-Gewichte.

    Warum nicht einfach FFT-Bins auf Pitch-Classes summieren (gemessen):
      * Im linearen Frequenzraster bekommt jede Pitch-Class unterschiedlich viele
        Bins (F=235, A#=315). Zusammen mit dem Rauschboden verzerrt das die
        Chroma-Verteilung (gemessen: richtig war Fm, erkannt wurde A#m).
      * Tieffrequente Linien lecken in Nachbarhalbtöne.
    Stattdessen: lokale Weißung, dann pro Halbton über alle Oktaven mitteln.
    """
    spec = stft_mag(y, n_fft, hop).astype(np.float32)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    sel = (freqs >= fmin) & (freqs <= fmax)
    s = spec[sel]
    f = freqs[sel]
    if whiten:
        # Spektrales Weißen: Rauschboden und Bassdrone auf ~1 drücken, tonale
        # Linien bleiben als Spitzen stehen. Der Nenner kommt aus dem Spektrum
        # selbst, nicht aus dem Zeitsignal.
        from scipy.ndimage import uniform_filter1d

        sm = uniform_filter1d(s, size=15, axis=0, mode="nearest")
        s = s / (sm + 1e-6)

    # Halbton-Mittenfrequenzen als aufeinanderfolgende Reihe (interp ist 1D)
    n_lo = 24
    n_hi = int(np.floor(12 * np.log2(fmax / 440.0) + 69))
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

    # Pro Frame normieren: Basslastigkeit einer Aufnahme darf die Tonart nicht bestimmen
    norm = np.linalg.norm(ch, axis=0, keepdims=True) + 1e-9
    ch = ch / norm
    # Frame-Gewicht aus dem Zeitbereich (RMS), NICHT aus spec**2: die
    # Spektralbeträge liegen bei ~1e5, das Quadrat läuft in float32 über.
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
    """Lineare Interpolation einer Frequenz x über alle Frames (vektorisiert)."""
    i = int(np.searchsorted(f, x))
    i = min(max(i, 1), f.size - 1)
    x0, x1 = f[i - 1], f[i]
    t = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
    return s[i - 1] * (1.0 - t) + s[i] * t


def _detect_key_from_audio(y: np.ndarray, sr: int = SR, *,
                           profiles: str = "kk") -> dict:
    """Tonart per Profilkorrelation auf dem Chroma-Vektor. Gibt Kandidaten + Marge.

    Erwartet ein Signal bei der übergebenen Rate `sr` (Vorgabe SR = 44,1 kHz) und
    resampelt intern auf die Chroma-Rate. Die Rate MUSS zum Puffer passen; sie ist
    deshalb Pflichtinformation und wird nicht geraten.
    """
    y_c = resample(y, sr, SR_CHROMA) if sr != SR_CHROMA else y
    ch, w = chroma(y_c, sr=SR_CHROMA)
    if ch.size == 0 or w.sum() <= 0:
        return {"camelot": None, "key_name": None, "score": 0.0,
                "margin": 0.0, "mode_conf": 0.0, "candidates": []}
    mean = np.average(ch, axis=1, weights=w)
    # Bekannte Grenze: Das Chroma wird pro Frame auf Einheitsnorm gebracht, der
    # Mittelwert ist daher immer flach (max ~0.45). Ein Schwellwert auf "tonale
    # Substanz" trennt perkussives Material deshalb nicht zuverlässig (gemessen:
    # Peakiness 0.49 für nur-Drums vs. 0.40 für nur-Pad). Die Entscheidung stützt
    # sich auf die Marge, nicht auf einen Absolutwert.
    major, minor = _PROFILES[profiles]
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
        "camelot": _CAMELOT.get((best_pc, best_mode)),
        "key_name": (_KEY_NAMES_MAJOR if best_mode else _KEY_NAMES_MINOR)[best_pc],
        "score": round(best_r, 3),
        "margin": round(best_r - second_r, 3),
        "mode_conf": round(mode_conf, 3),
        "candidates": [
            {"camelot": _CAMELOT.get((pc, mode)),
             "key_name": (_KEY_NAMES_MAJOR if mode else _KEY_NAMES_MINOR)[pc],
             "r": round(r, 3)}
            for r, pc, mode in results[:4]
        ],
    }


def detect_key(path: Path) -> tuple[str, str] | None:
    """Erkennt die musikalische Tonart eines Audio-Files.

    Gibt ein Tuple (camelot_key, key_name) zurück, z.B. ("5A", "Cm"),
    oder None wenn die Erkennung fehlschlägt.

    Verwendet oktav-gemitteltes Chroma und Krumhansl-Kessler-Key-Profiling.
    """
    try:
        audio = _decode_for_analysis(path)
        if audio is None:
            return None
        result = _detect_key_from_audio(audio)
        if result["camelot"] is None or result["key_name"] is None:
            return None
        return result["camelot"], result["key_name"]
    except Exception as e:
        log.warning("Key-Erkennung fehlgeschlagen für %s: %s", path.name, e)
        return None


def compute_energy_features(path: Path) -> dict[str, float | None]:
    """Berechnet energiebezogene Audio-Features via librosa aus Mono-Audio (22050 Hz, 60s Offset 30s).

    Rückgabe: {"spectral_centroid_hz": float | None, "rms": float | None}
    Bei fehlendem librosa oder defekter/unlesbarer Datei: None-Werte, wirft nie.
    """
    leer: dict[str, float | None] = {"spectral_centroid_hz": None, "rms": None}
    try:
        import librosa
    except ImportError:
        log.warning("librosa nicht installiert – Energie-Features übersprungen")
        return dict(leer)

    try:
        audio = _decode_audio(path, sr=22050, duration=60, offset=30)
        if audio is None:
            return dict(leer)
        centroid = librosa.feature.spectral_centroid(y=audio, sr=22050)
        rms = librosa.feature.rms(y=audio)
        return {
            "spectral_centroid_hz": float(np.mean(centroid)),
            "rms": float(np.mean(rms)),
        }
    except Exception as e:
        log.warning("Energie-Features fehlgeschlagen für %s: %s", path.name, e)
        return dict(leer)


def analyze_track(path: Path, *, want_bpm: bool = True, want_key: bool = True,
                  snap_to: tuple[float, float] | None = None) -> dict:
    """Führt fusionierte BPM- und Key-Erkennung mit einem einzigen Audio-Decode durch.

    snap_to kommt aus der Config (z. B. (search.bpm_min, search.bpm_max)) und holt
    halb-/doppeltzeitige Erkennungen in das Genre-Fenster.

    Returns:
        {
            "bpm": float | None,
            "bpm_confidence": float,   # 0.0 bis 1.0
            "bpm_octave": int,         # 1 = unverändert, 2 = verdoppelt, 0 = halbiert
            "key_camelot": str | None,
            "key_name": str | None,
            "key_confidence": float,   # Mode-Konfidenz (0.0 bis 1.0)
            "key_margin": float,       # Marge zum zweitbesten Key-Kandidaten
        }
    """
    result: dict = {
        "bpm": None, "bpm_confidence": 0.0, "bpm_octave": 1,
        "key_camelot": None, "key_name": None,
        "key_confidence": 0.0, "key_margin": 0.0,
    }
    try:
        audio = _decode_for_analysis(path)
        if audio is None:
            return result
        if want_bpm:
            tempo = detect_tempo(audio, snap_to=snap_to)
            result["bpm"] = tempo.bpm
            result["bpm_confidence"] = round(tempo.confidence, 3)
            result["bpm_octave"] = tempo.octave
        if want_key:
            key = _detect_key_from_audio(audio)
            result["key_camelot"] = key["camelot"]
            result["key_name"] = key["key_name"]
            result["key_confidence"] = key["mode_conf"]
            result["key_margin"] = key["margin"]
    except Exception as e:
        log.warning("Audio-Analyse fehlgeschlagen für %s: %s", path.name, e)
    return result
