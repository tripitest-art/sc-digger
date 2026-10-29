"""Stem-basierte Subbass- und Breakdown-Analyse der Demucs-Stems.

Analysiert die Demucs-Stems ``bass.wav`` (< 100 Hz) und ``drums.wav``, um Drops
frequenzgenau zu verifizieren (Subbass-Check, Fake-Drop-Korrektur) und Kick-Pausen
als ``silence``/``groove`` zu typisieren. Reine Signal- und Audio-Logik ohne
Inferenz-Abhängigkeit, deterministisch und synthetisch testbar.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import subprocess
from typing import Sequence

import numpy as np

from .quality import LOCAL_ONLY

log = logging.getLogger(__name__)

_SR = 22050
_N_FFT = 1024
_HOP_LENGTH = 512
_SUBBASS_MAX_HZ = 100.0      # Subbass-Band für den Drop-Check
_QUIET_RATIO = 0.01          # Subbass-Pegel unter diesem Peak-Anteil gilt als Pause
_DOWNBEAT_TOL_S = 0.15       # Fenster um einen Downbeat, in dem die Energie gemessen wird
_FAKE_DROP_TOL_S = 0.1       # Versatz ab dieser Größe gilt als Fake-Drop-Korrektur
_FIRST_KICK_TOL_RATIO = 0.5  # Anteil des Peaks, ab dem ein Subbass-Einsatz beginnt


@dataclass(frozen=True)
class Breakdown:
    """Repräsentiert einen erkannten Breakdown anhand der Kick-Pause."""
    start_s: float
    end_s: float
    kind: str  # "silence" | "groove"
    drum_rms_ratio: float  # RMS im Break relativ zum Gesamt-Drum-RMS


@dataclass(frozen=True)
class Drop:
    """Repräsentiert einen validierten Subbass-Drop."""
    start_s: float
    energy_ratio: float  # Erreichte Subbass-Energie relativ zum Referenz-Peak
    is_fake_drop_corrected: bool = False
    offset_s: float = 0.0  # Zeitlicher Versatz gegenüber gemeldetem Start


@dataclass(frozen=True)
class StemAnalysisResult:
    """Ergebnis der vollständigen Stem-Strukturanalyse."""
    breakdowns: list[Breakdown]
    drops: list[Drop]
    first_kick_s: float | None


def _decode_audio(path: Path | str, sr: int = _SR) -> np.ndarray | None:
    """Dekodiert den Track per ffmpeg nach Mono-Float-PCM. None bei Fehlern."""
    p = Path(path)
    if not p.is_file():
        return None
    cmd = [
        "ffmpeg", "-v", "error", *LOCAL_ONLY,
        "-i", str(p),
        "-ac", "1", "-ar", str(sr), "-f", "f32le", "-",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=180)
        if r.returncode != 0 or len(r.stdout) == 0:
            return None
        audio = np.frombuffer(r.stdout, dtype=np.float32)
        if audio.size < sr:
            return None
        return audio
    except Exception as e:
        log.warning("Fehler beim Dekodieren von %s: %s", p, e)
        return None


def _subbass_envelope(path: Path | str) -> tuple[np.ndarray, np.ndarray] | None:
    """Frame-Energie im Subbass-Band (< 100 Hz) als (zeiten, energien). None bei Fehlern."""
    try:
        import librosa
    except ImportError:
        log.warning("librosa nicht installiert – Stem-Analyse übersprungen")
        return None

    audio = _decode_audio(path)
    if audio is None:
        return None

    try:
        spek = np.abs(librosa.stft(audio, n_fft=_N_FFT, hop_length=_HOP_LENGTH))
        freqs = librosa.fft_frequencies(sr=_SR, n_fft=_N_FFT)
        band = np.where(freqs <= _SUBBASS_MAX_HZ)[0]
        if band.size == 0:
            return None
        energy = np.sum(spek[band, :] ** 2, axis=0)
        times = librosa.frames_to_time(
            np.arange(energy.shape[0]), sr=_SR, hop_length=_HOP_LENGTH
        )
        return times, energy
    except Exception as e:
        log.warning("Subbass-Envelope für %s fehlgeschlagen: %s", path, e)
        return None


def _energy_near(times: np.ndarray, energy: np.ndarray, t: float,
                 tol: float = _DOWNBEAT_TOL_S) -> float:
    """Maximale Frame-Energie im Fenster ±tol um den Zeitpunkt t (0.0, wenn keins)."""
    mask = np.abs(times - t) <= tol
    if not np.any(mask):
        return 0.0
    return float(np.max(energy[mask]))


def classify_breakdown_type(
    drums_path: Path | str,
    start_s: float,
    end_s: float,
    *,
    groove_threshold: float = 0.15,
) -> tuple[str, float]:
    """Klassifiziert [start_s, end_s] anhand des Drums-Stems als "groove"/"silence".

    Das Verhältnis ist der RMS des Abschnitts relativ zum RMS des gesamten
    Drums-Stems. >= groove_threshold ergibt ("groove", ratio), sonst
    ("silence", ratio). Bei Fehlern, fehlender Datei, ungültiger Zeitspanne oder
    Stille im Track kommt ("silence", 0.0) zurück. Wirft nie.
    """
    try:
        if start_s < 0 or end_s <= start_s:
            return ("silence", 0.0)

        audio = _decode_audio(drums_path)
        if audio is None:
            return ("silence", 0.0)

        total_rms = float(np.sqrt(np.mean(audio ** 2)))
        if total_rms <= 0.0:
            return ("silence", 0.0)

        i0 = max(0, int(start_s * _SR))
        i1 = min(len(audio), int(end_s * _SR))
        if i1 <= i0:
            return ("silence", 0.0)

        seg_rms = float(np.sqrt(np.mean(audio[i0:i1] ** 2)))
        ratio = seg_rms / total_rms
        kind = "groove" if ratio >= groove_threshold else "silence"
        return (kind, round(ratio, 4))
    except Exception as e:
        log.warning("Breakdown-Typisierung für %s fehlgeschlagen: %s", drums_path, e)
        return ("silence", 0.0)


def find_subbass_drop(
    bass_path: Path | str,
    search_start_s: float,
    *,
    downbeats: Sequence[float] | None = None,
    bpm: float | None = None,
    search_window_s: float = 12.0,
    min_energy_ratio: float = 0.75,
) -> Drop | None:
    """Findet den echten Subbass-Einsatz (< 100 Hz) nach search_start_s.

    Untersucht das Fenster [search_start_s, search_start_s + search_window_s].
    Der gefundene Zeitpunkt ist der erste, an dem die Subbass-Energie mindestens
    min_energy_ratio des maximalen Subbass-Peaks des Tracks erreicht; sind
    downbeats angegeben, wird bevorzugt auf einen Downbeat mit ausreichend
    Energie gerastet. Liegt der Einsatz später als search_start_s (Toleranz
    > 0.1 s), gilt der Drop als Fake-Drop-korrigiert. Kein Treffer im Fenster
    oder unlesbare Datei: None. bpm wird für die Schnittstelle akzeptiert, hier
    aber nicht benötigt. Wirft nie.
    """
    try:
        env = _subbass_envelope(bass_path)
        if env is None:
            return None
        times, energy = env
        if energy.size == 0:
            return None
        peak = float(np.max(energy))
        if peak <= 0.0:
            return None

        threshold = min_energy_ratio * peak
        window_end = search_start_s + search_window_s

        onset_s: float | None = None
        onset_energy = 0.0

        if downbeats:
            for raw in downbeats:
                d = float(raw)
                if d < search_start_s or d > window_end:
                    continue
                e = _energy_near(times, energy, d)
                if e >= threshold:
                    onset_s = d
                    onset_energy = e
                    break

        if onset_s is None:
            mask = (times >= search_start_s) & (times <= window_end) & (energy >= threshold)
            hits = np.where(mask)[0]
            if hits.size == 0:
                return None
            onset_s = float(times[hits[0]])
            onset_energy = float(energy[hits[0]])

        offset = onset_s - search_start_s
        corrected = offset > _FAKE_DROP_TOL_S
        return Drop(
            start_s=round(onset_s, 3),
            energy_ratio=round(onset_energy / peak, 4),
            is_fake_drop_corrected=corrected,
            offset_s=round(offset, 3) if corrected else 0.0,
        )
    except Exception as e:
        log.warning("Subbass-Drop-Analyse für %s fehlgeschlagen: %s", bass_path, e)
        return None


def analyze_stems(
    bass_path: Path | str,
    drums_path: Path | str,
    *,
    downbeats: Sequence[float] | None = None,
    bpm: float | None = None,
    min_pause_s: float = 6.0,
    groove_threshold: float = 0.15,
    min_drop_energy_ratio: float = 0.75,
) -> StemAnalysisResult:
    """Führt die kombinierte Analyse von Bass- und Drums-Stems durch.

    1. Ermittelt Kick-Pausen >= min_pause_s im Bass-Stem (< 100 Hz).
    2. Typisiert jede Pause per classify_breakdown_type in 'silence' oder 'groove'.
    3. Sucht am Ende jeder Pause per find_subbass_drop nach dem echten Drop.
    4. Ermittelt first_kick_s (erster Subbass-Einsatz im Track).

    Gibt StemAnalysisResult zurück. Bei Fehlern leere Listen und first_kick_s=None.
    Intro-/Outro-Stille zählt nicht als Kick-Pause. Wirft nie.
    """
    empty = StemAnalysisResult(breakdowns=[], drops=[], first_kick_s=None)
    try:
        env = _subbass_envelope(bass_path)
        if env is None:
            return empty
        times, energy = env
        if energy.size == 0:
            return empty
        peak = float(np.max(energy))
        if peak <= 0.0:
            return empty

        first_hits = np.where(energy >= _FIRST_KICK_TOL_RATIO * peak)[0]
        first_kick_s = round(float(times[first_hits[0]]), 3) if first_hits.size else None

        quiet = energy < (_QUIET_RATIO * peak)
        n = len(quiet)
        breakdowns: list[Breakdown] = []
        drops: list[Drop] = []

        i = 0
        while i < n:
            if not quiet[i]:
                i += 1
                continue
            run_start = i
            j = i
            while j < n and quiet[j]:
                j += 1
            run_end = j - 1
            i = j

            # Intro- und Outro-Stille ist kein Breakdown (es fehlt die Kick davor/danach).
            if run_start == 0 or run_end == n - 1:
                continue

            start_s = float(times[run_start])
            end_s = float(times[run_end])
            if (end_s - start_s) < min_pause_s:
                continue

            kind, ratio = classify_breakdown_type(
                drums_path, start_s, end_s, groove_threshold=groove_threshold
            )
            breakdowns.append(
                Breakdown(
                    start_s=round(start_s, 3),
                    end_s=round(end_s, 3),
                    kind=kind,
                    drum_rms_ratio=ratio,
                )
            )

            drop = find_subbass_drop(
                bass_path,
                end_s,
                downbeats=downbeats,
                bpm=bpm,
                min_energy_ratio=min_drop_energy_ratio,
            )
            if drop is not None:
                drops.append(drop)

        return StemAnalysisResult(
            breakdowns=breakdowns, drops=drops, first_kick_s=first_kick_s
        )
    except Exception as e:
        log.warning("Stem-Analyse für %s/%s fehlgeschlagen: %s", bass_path, drums_path, e)
        return empty
