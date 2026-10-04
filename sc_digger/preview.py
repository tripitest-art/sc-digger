"""Live-Preview: energiereichstes Segment einer Audiodatei als OGG-Opus-Snippet.

Grundlage für die Sprachnachrichten-Vorschau im Telegram-Bot (Teil 2 von Epic #53).
Die Analyse dekodiert das Audio per ffmpeg zu Mono-Float-Samples und sucht das Fenster
mit der höchsten RMS-Energie. Die Extraktion danach erzeugt ein bandbreitenschonendes
Opus-Snippet, das klein genug für den Telegram-Versand ist.

Wie der Rest der Audio-Kette arbeitet das Modul defensiv: kaputte oder fehlende Dateien,
unbekannte Formate und ffmpeg-Fehler führen zu einem definierten Rückgabewert statt zu
einer Exception. Der Bot darf an einer kaputten Datei nie sterben.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# Analyse auf 8 kHz Mono: reicht für eine RMS-Energie-Kurve und hält das Dekodieren
# langer Tracks schnell und speicherarm.
_ANALYSIS_SAMPLE_RATE = 8000
_ANALYSIS_TIMEOUT_S = 300
_EXTRACT_TIMEOUT_S = 300
_PROBE_TIMEOUT_S = 60


def _decode_mono(path: Path, sample_rate: int) -> np.ndarray | None:
    """Dekodiert eine Audiodatei per ffmpeg zu Mono-Float32-Samples. None bei Fehler."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-v", "error",
                "-i", str(path),
                "-vn", "-ac", "1", "-ar", str(sample_rate),
                "-f", "f32le", "-",
            ],
            capture_output=True,
            timeout=_ANALYSIS_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("Preview-Analyse fehlgeschlagen für %s: %s", path, e)
        return None

    if result.returncode != 0:
        log.warning(
            "Preview-Analyse fehlgeschlagen für %s: %s",
            path,
            result.stderr.decode(errors="replace").strip()[:200],
        )
        return None

    audio = np.frombuffer(result.stdout, dtype="<f4")
    if audio.size == 0:
        return None
    return audio


def _remove(path: Path) -> None:
    """Entfernt eine halbfertige Ausgabedatei, ohne selbst zu scheitern."""
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def _has_audio_stream(path: Path) -> bool:
    """Prüft per ffprobe, ob die Datei wirklich einen Audio-Stream enthält.

    ffmpeg schreibt auch dann eine Datei (nur den Ogg-Kopf), wenn der Schnittbereich
    außerhalb des Materials liegt. Eine größenbasierte Prüfung würde das als Erfolg
    werten und der Bot verschickte eine leere Sprachnachricht.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=codec_name",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            capture_output=True,
            timeout=_PROBE_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("Preview-Prüfung fehlgeschlagen für %s: %s", path, e)
        return False

    return result.returncode == 0 and bool(result.stdout.strip())


def find_loudest_segment(
    audio_path: Path,
    segment_duration_s: float = 20.0,
) -> float:
    """Start-Offset in Sekunden des energiereichsten Segments der Länge segment_duration_s.

    Das Audio wird in Fenster der Ziel-Länge zerlegt (Halbschritt als Überlappung), je
    Fenster die RMS-Energie über die kumulierte Quadratsumme berechnet und der Start des
    stärksten Fensters geliefert. Liefert 0.0, falls die Datei fehlt, kürzer als
    segment_duration_s ist oder ein Fehler auftritt. Wirft nie.
    """
    try:
        path = Path(audio_path)
        duration = float(segment_duration_s)
        if not path.is_file() or duration <= 0:
            return 0.0

        sample_rate = _ANALYSIS_SAMPLE_RATE
        audio = _decode_mono(path, sample_rate)
        if audio is None:
            return 0.0

        window = int(round(duration * sample_rate))
        if window <= 0 or audio.size < window:
            return 0.0

        samples = audio.astype(np.float64)
        cumulative = np.concatenate(([0.0], np.cumsum(np.square(samples))))

        hop = max(1, sample_rate // 2)
        starts = list(range(0, audio.size - window + 1, hop))
        last_start = audio.size - window
        if starts[-1] != last_start:
            # Fenster am Dateiende nicht verpassen, wenn die Länge kein Vielfaches des Hops ist.
            starts.append(last_start)

        best_start = 0
        best_energy = -1.0
        for start in starts:
            energy = cumulative[start + window] - cumulative[start]
            if energy > best_energy:
                best_energy = energy
                best_start = start

        return round(best_start / sample_rate, 3)
    except Exception as e:
        log.warning("Lautestes Segment für %s nicht ermittelbar: %s", audio_path, e)
        return 0.0


def extract_preview_from_url(
    source_url: str,
    output_path: Path,
    *,
    start_s: float = 0.0,
    duration_s: float = 20.0,
    bitrate_kbps: int = 64,
) -> bool:
    """Erzeugt aus source_url ein OGG-Opus-Snippet (max. duration_s, ab start_s)
    an output_path per ffmpeg (-ss/-t). Wirft nie; bei fehlendem ffmpeg, Abbruch
    oder leerer Datei False und räumt eine evtl. angelegte Datei wieder weg.
    """
    out = Path(output_path)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-ss", f"{float(start_s):.3f}",
                "-t", f"{float(duration_s):g}",
                "-i", str(source_url),
                "-vn", "-c:a", "libopus", "-b:a", f"{int(bitrate_kbps)}k",
                str(out),
            ],
            capture_output=True,
            timeout=_EXTRACT_TIMEOUT_S,
        )
        if result.returncode != 0:
            log.warning(
                "Stream-Preview-Extraktion fehlgeschlagen: %s",
                result.stderr.decode(errors="replace").strip()[:200],
            )
            _remove(out)
            return False

        if not out.is_file() or out.stat().st_size <= 0:
            _remove(out)
            return False
        return True
    except Exception as e:
        log.warning("Stream-Preview-Extraktion fehlgeschlagen: %s", e)
        _remove(out)
        return False


def extract_preview(
    input_path: Path,
    output_path: Path,
    *,
    start_s: float | None = None,
    duration_s: float = 20.0,
    bitrate_kbps: int = 64,
) -> bool:
    """Schneidet ein Opus-Snippet aus input_path nach output_path.

    Ohne start_s wird das energiereichste Segment (find_loudest_segment) verwendet.
    Zielverzeichnisse werden bei Bedarf erstellt. True, wenn die Zieldatei existiert,
    größer als 0 Bytes ist und einen Audio-Stream enthält, sonst False (halbfertige
    Dateien werden entfernt). Wirft nie.
    """
    out = Path(output_path)
    try:
        source = Path(input_path)
        if not source.is_file():
            log.warning("Preview nicht möglich, Datei fehlt: %s", source)
            return False

        start = find_loudest_segment(source, duration_s) if start_s is None else float(start_s)
        out.parent.mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-ss", f"{start:.3f}",
                "-t", f"{float(duration_s):.3f}",
                "-i", str(source),
                "-vn", "-c:a", "libopus", "-b:a", f"{int(bitrate_kbps)}k",
                str(out),
            ],
            capture_output=True,
            timeout=_EXTRACT_TIMEOUT_S,
        )
        if result.returncode != 0:
            log.warning(
                "Preview-Extraktion fehlgeschlagen für %s: %s",
                source,
                result.stderr.decode(errors="replace").strip()[:200],
            )
            _remove(out)
            return False

        if not out.is_file() or out.stat().st_size <= 0 or not _has_audio_stream(out):
            _remove(out)
            return False
        return True
    except Exception as e:
        log.warning("Preview-Extraktion fehlgeschlagen für %s: %s", input_path, e)
        _remove(out)
        return False
