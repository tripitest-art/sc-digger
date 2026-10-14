"""Strukturanalyse: Erkennung von Kicks, Segmenten und erstem Kick.

Erkennt, wo in einem Track die Kick läuft, und leitet daraus den ersten Kick
und die Segmente intro, main, breakdown, outro ab.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Sequence
import subprocess
import sys

import numpy as np

from sc_digger import phrase

log = logging.getLogger(__name__)

MIN_BREAKDOWN_S = 6.0   # kickloser Abschnitt in der Mitte ab dieser Länge ist ein Breakdown
MIN_EDGE_S = 2.0        # kickloser Abschnitt am Anfang/Ende ab dieser Länge ist Intro/Outro


@dataclass(frozen=True)
class Segment:
    """Repräsentiert ein strukturelles Segment eines Tracks."""
    kind: str       # "intro" | "main" | "breakdown" | "outro"
    start_s: float
    end_s: float


def _decode_audio(path: Path, sr: int = 22050) -> np.ndarray | None:
    """Dekodiert den gesamten Track per ffmpeg nach Mono-Float-PCM."""
    if not path.is_file():
        return None
    cmd = [
        "ffmpeg", "-v", "error",
        "-i", str(path),
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
        log.warning("Fehler beim Dekodieren von %s: %s", path, e)
        return None


def _detect_kick_runs(path: Path) -> tuple[np.ndarray, list[tuple[int, int]], float] | None:
    """Ermittelt Kick-Peaks und zusammenhängende Kick-Strecken.
    
    Returns:
        (peak_times, valid_runs, duration) oder None.
        valid_runs ist eine Liste von (start_peak_idx, end_peak_idx).
    """
    try:
        import librosa

        sr = 22050
        audio = _decode_audio(path, sr=sr)
        if audio is None or len(audio) < sr:
            return None

        duration = len(audio) / sr

        n_fft = 1024
        hop_length = 220  # ca. 10 ms Auflösung
        S = np.abs(librosa.stft(audio, n_fft=n_fft, hop_length=hop_length))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)

        # Bassband 40–130 Hz für Kick-Analyse
        idx = np.where((freqs >= 40) & (freqs <= 130))[0]
        if len(idx) == 0:
            return None

        band_energy = np.sum(S[idx, :], axis=0)
        diff = np.diff(band_energy, prepend=band_energy[0])
        flux = np.maximum(0, diff)

        max_flux = float(np.max(flux)) if len(flux) > 0 else 0.0
        if max_flux <= 0.0:
            return None

        times = librosa.frames_to_time(np.arange(len(flux)), sr=sr, hop_length=hop_length)
        # Peak-Picking mit relativem Schwellwert
        peaks = librosa.util.peak_pick(
            flux,
            pre_max=3,
            post_max=3,
            pre_avg=3,
            post_avg=3,
            delta=max_flux * 0.15,
            wait=8,
        )
        if len(peaks) < 4:
            return None

        peak_times = times[peaks]

        # Kick-Strecken gruppieren: Pausen >= MIN_BREAKDOWN_S trennen Strecken
        gaps = np.diff(peak_times)
        break_indices = np.where(gaps >= MIN_BREAKDOWN_S)[0]

        run_starts = [0] + [i + 1 for i in break_indices]
        run_ends = list(break_indices) + [len(peak_times) - 1]

        valid_runs: list[tuple[int, int]] = []
        for s_idx, e_idx in zip(run_starts, run_ends):
            # Eine valide Kick-Strecke muss mindestens 4 Peaks haben
            if (e_idx - s_idx + 1) >= 4:
                valid_runs.append((s_idx, e_idx))

        if not valid_runs:
            return None

        return peak_times, valid_runs, duration
    except Exception as e:
        log.warning("Fehler bei Kick-Erkennung für %s: %s", path, e)
        return None


def find_first_kick(path: Path, *, search_s: float = 60.0) -> float | None:
    """Zeitpunkt des ersten Kick-Einsatzes in Sekunden (Beginn des Kicks, nicht Abklingen), auf
    Millisekunden gerundet. None, wenn im Fenster [0, search_s] kein Kick-Muster beginnt, die Datei
    nicht lesbar ist oder kein Kick erkannt wird. Wirft nie (log.warning bei Fehlern).
    """
    res = _detect_kick_runs(path)
    if res is None:
        return None

    peak_times, valid_runs, _ = res
    first_kick = float(peak_times[valid_runs[0][0]])

    if first_kick <= search_s:
        return round(first_kick, 3)
    return None


def analyze_structure(path: Path) -> list[Segment]:
    """Segmente über die ganze Spieldauer, lückenlos und in zeitlicher Reihenfolge.
    - "intro": kickloser Abschnitt am Anfang, mindestens MIN_EDGE_S lang.
    - "outro": kickloser Abschnitt am Ende, mindestens MIN_EDGE_S lang.
    - "breakdown": kickloser Abschnitt zwischen zwei Kick-Abschnitten, mindestens MIN_BREAKDOWN_S lang.
    - "main": alles dazwischen, in dem die Kick läuft.
    Keine erkennbare Kick im ganzen Track, unlesbare Datei oder Fehler: []. Wirft nie.
    """
    res = _detect_kick_runs(path)
    if res is None:
        return []

    peak_times, valid_runs, duration = res

    # Typischen Beat-Abstand ermitteln
    intervals: list[float] = []
    for s_idx, e_idx in valid_runs:
        if e_idx > s_idx:
            run_gaps = np.diff(peak_times[s_idx : e_idx + 1])
            intervals.extend(run_gaps[run_gaps < 1.5])
    beat_step = float(np.median(intervals)) if intervals else 0.4

    segments: list[Segment] = []

    first_run_start = float(peak_times[valid_runs[0][0]])
    if first_run_start >= MIN_EDGE_S:
        intro_end = round(first_run_start, 3)
        segments.append(Segment("intro", 0.0, intro_end))
        current_time = intro_end
    else:
        current_time = 0.0

    for i, (s_idx, e_idx) in enumerate(valid_runs):
        run_last = float(peak_times[e_idx])
        is_last_run = (i == len(valid_runs) - 1)
        run_end = min(duration, run_last + beat_step)

        if is_last_run:
            if (duration - run_end) >= MIN_EDGE_S:
                main_end = round(run_end, 3)
                segments.append(Segment("main", current_time, main_end))
                segments.append(Segment("outro", main_end, round(duration, 3)))
            else:
                segments.append(Segment("main", current_time, round(duration, 3)))
        else:
            next_run_start = float(peak_times[valid_runs[i + 1][0]])
            main_end = round(run_end, 3)
            break_start = main_end
            break_end = round(next_run_start, 3)

            segments.append(Segment("main", current_time, main_end))
            segments.append(Segment("breakdown", break_start, break_end))
            current_time = break_end

    return segments


def _resolve_segments(
    source: Sequence[Segment] | Path | str,
) -> list[Segment]:
    """Ermittelt Segmentketten aus einer Sequenz von Segmenten oder einem Dateipfad.

    Eine Sequenz wird unverändert (als Liste) übernommen; ein Pfad/Text wird per
    analyze_structure() analysiert. Ob eine Zeichenkette ein Pfad oder eine
    Segmentliste ist, entscheidet das erste Element: Strings sind Pfade. Bei Fehlern
    oder leeren Quellen kommt [] zurück.
    """
    if isinstance(source, (str, Path)):
        try:
            return analyze_structure(Path(source))
        except Exception as e:
            log.warning("Struktur-Analyse fehlgeschlagen für %s: %s", source, e)
            return []
    try:
        return list(source)
    except Exception as e:
        log.warning("Segment-Quelle nicht lesbar: %s", e)
        return []


def find_drop_start(
    source: Sequence[Segment] | Path | str,
    *,
    bpm: float | None = None,
    first_kick_s: float | None = None,
    phrase_align: bool = False,
    downbeats: Sequence[float] | None = None,
) -> float | None:
    """Zeitpunkt des Kick-Wiedereinsatzes nach dem ersten Breakdown in Sekunden.
    - source kann eine Sequenz von Segmenten (aus analyze_structure) oder ein Dateipfad sein.
    - Sucht das erste Segment mit kind == 'breakdown'.
    - Gibt dessen end_s zurück (auf Millisekunden gerundet), an dem die Kick wieder einsetzt.
    - Ist phrase_align=True, bpm > 0 und first_kick_s vorhanden, wird der gefundene Zeitpunkt
      per phrase.quantize_to_phrase auf das 16/32-Beat-Phrasengitter ausgerichtet.
    - Gibt None zurück, wenn kein Breakdown existiert, keine Segmente vorhanden sind oder die
      Datei nicht lesbar ist. Wirft nie.
    """
    try:
        for seg in _resolve_segments(source):
            if seg.kind == "breakdown":
                drop = round(float(seg.end_s), 3)
                if phrase_align and bpm is not None and bpm > 0 and first_kick_s is not None:
                    drop = phrase.quantize_to_phrase(drop, first_kick_s, bpm, downbeats=downbeats)
                return drop
        return None
    except Exception as e:
        log.warning("Drop-Start für %s nicht ermittelbar: %s", source, e)
        return None


def calculate_breakdown_ratio(source: Sequence[Segment] | Path | str) -> float:
    """Anteil kickloser Zeit zwischen Intro-Ende und Outro-Start als float von 0.0 bis 1.0.
    - source kann eine Sequenz von Segmenten (aus analyze_structure) oder ein Dateipfad sein.
    - Intro-Ende: end_s des ersten Segments, wenn kind == 'intro', sonst start_s des ersten Segments.
    - Outro-Start: start_s des letzten Segments, wenn kind == 'outro', sonst end_s des letzten Segments.
    - Hauptteil-Dauer = Outro-Start - Intro-Ende.
    - Summiert die Dauern aller Segmente mit kind == 'breakdown' innerhalb des Hauptteils.
    - Ergebnis = sum(breakdown_dauern) / Hauptteil-Dauer, gerundet auf 4 Nachkommastellen.
    - Ist Hauptteil-Dauer <= 0: 0.0. Wirft nie.
    """
    try:
        segments = _resolve_segments(source)
        if not segments:
            return 0.0

        erste = segments[0]
        intro_end = float(erste.end_s) if erste.kind == "intro" else float(erste.start_s)

        letzte = segments[-1]
        outro_start = float(letzte.start_s) if letzte.kind == "outro" else float(letzte.end_s)

        main_duration = outro_start - intro_end
        if main_duration <= 0:
            return 0.0

        breakdown_duration = sum(
            float(s.end_s) - float(s.start_s)
            for s in segments
            if s.kind == "breakdown" and float(s.start_s) >= intro_end and float(s.end_s) <= outro_start
        )
        return round(breakdown_duration / main_duration, 4)
    except Exception as e:
        log.warning("Breakdown-Anteil für %s nicht ermittelbar: %s", source, e)
        return 0.0


def main(argv: list[str] | None = None) -> int:
    """Kommandozeile: python -m sc_digger.structure <datei>. Ausgabe auf stdout:
      erster Kick: 6.40 s            (oder: kein Kick gefunden)
      intro          0.0 –     6.4 s   (je Segment eine Zeile: f"{kind:<10} {start:7.1f} – {end:7.1f} s")
    Rückgabe 0. Existiert die Datei nicht: "Datei nicht gefunden: <pfad>" ausgeben und 1 zurückgeben.
    """
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        print("Verwendung: python -m sc_digger.structure <datei>")
        return 1

    target = Path(argv[0])
    if not target.is_file():
        print(f"Datei nicht gefunden: {target}")
        return 1

    fk = find_first_kick(target)
    if fk is not None:
        print(f"erster Kick: {fk:.2f} s")
    else:
        print("kein Kick gefunden")

    segs = analyze_structure(target)
    for seg in segs:
        print(f"{seg.kind:<10} {seg.start_s:7.1f} \u2013 {seg.end_s:7.1f} s")

    return 0


if __name__ == "__main__":
    sys.exit(main())
