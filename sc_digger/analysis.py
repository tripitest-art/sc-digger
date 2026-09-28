"""Audio-Analyse: BPM-Erkennung und Tonart-Erkennung (Camelot-Key).

Nutzt librosa für zuverlässige BPM- und Key-Erkennung direkt aus dem Audiomaterial,
unabhängig von (oft fehlenden) Metadaten der Uploader.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

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
    # Dur (B-Notation)
    (0, 1): "8B",   # C major
    (1, 1): "3B",   # C# major
    (2, 1): "10B",  # D major
    (3, 1): "5B",   # D# / Eb major
    (4, 1): "12B",  # E major
    (5, 1): "7B",   # F major
    (6, 1): "4B",   # F# major
    (7, 1): "11B",  # G major
    (8, 1): "6B",   # G# / Ab major
    (9, 1): "1B",   # A major
    (10, 1): "8B",  # A# / Bb major
    (11, 1): "3B",  # B major
}

# Pitch-Class -> Open-Key-Notation (Alternative zu Camelot, für ID3-Tags)
_KEY_NAMES_MINOR = ["Cm", "C#m", "Dm", "D#m", "Em", "Fm", "F#m", "Gm", "G#m", "Am", "A#m", "Bm"]
_KEY_NAMES_MAJOR = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


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
    if audio.size < sr * 5:
        # Offset war zu weit -> vom Anfang versuchen
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


def detect_bpm(path: Path) -> float | None:
    """Erkennt das Tempo (BPM) eines Audio-Files via Onset-basierter Autokorrelation.

    Gibt den wahrscheinlichsten BPM-Wert zurück, oder None wenn die Erkennung
    fehlschlägt. Optimiert für den Bereich 90–200 BPM (Techno/Schranz).
    """
    try:
        import librosa
    except ImportError:
        log.warning("librosa nicht installiert – BPM-Erkennung übersprungen")
        return None

    try:
        audio = _decode_audio(path, sr=22050, duration=60, offset=30)
        if audio is None:
            return None
        tempo = librosa.beat.beat_track(y=audio, sr=22050, start_bpm=155)[0]
        # librosa gibt seit 0.10 ein Array zurück
        bpm = float(np.atleast_1d(tempo)[0])
        if bpm < 70:
            return None
        return round(bpm, 1)
    except Exception as e:
        log.warning("BPM-Erkennung fehlgeschlagen für %s: %s", path.name, e)
        return None


def detect_key(path: Path) -> tuple[str, str] | None:
    """Erkennt die musikalische Tonart eines Audio-Files.

    Gibt ein Tuple (camelot_key, key_name) zurück, z.B. ("5A", "Cm"),
    oder None wenn die Erkennung fehlschlägt.

    Verwendet Chroma-Features und Krumhansl-Schmuckler Key-Profiling.
    """
    try:
        import librosa
    except ImportError:
        log.warning("librosa nicht installiert – Key-Erkennung übersprungen")
        return None

    try:
        audio = _decode_audio(path, sr=22050, duration=60, offset=30)
        if audio is None:
            return None

        # Chroma-Features (Pitch-Class-Verteilung)
        chroma = librosa.feature.chroma_cqt(y=audio, sr=22050)
        chroma_mean = np.mean(chroma, axis=1)

        # Krumhansl-Kessler Profil-Korrelation
        major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                                   2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
        minor_profile = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                                   2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

        best_corr = -1.0
        best_pitch = 0
        best_mode = 0  # 0=Moll, 1=Dur

        for pitch_class in range(12):
            # Rotiere Chroma so, dass pitch_class an Position 0 steht
            rotated = np.roll(chroma_mean, -pitch_class)

            corr_major = float(np.corrcoef(rotated, major_profile)[0, 1])
            corr_minor = float(np.corrcoef(rotated, minor_profile)[0, 1])

            if corr_major > best_corr:
                best_corr = corr_major
                best_pitch = pitch_class
                best_mode = 1
            if corr_minor > best_corr:
                best_corr = corr_minor
                best_pitch = pitch_class
                best_mode = 0

        camelot = _CAMELOT.get((best_pitch, best_mode), "?")
        key_name = (_KEY_NAMES_MAJOR if best_mode == 1 else _KEY_NAMES_MINOR)[best_pitch]
        return camelot, key_name
    except Exception as e:
        log.warning("Key-Erkennung fehlgeschlagen für %s: %s", path.name, e)
        return None


def analyze_track(path: Path) -> dict:
    """Führt BPM- und Key-Erkennung durch und gibt ein Analyse-Dict zurück.

    Returns:
        {"bpm": float | None, "key_camelot": str | None, "key_name": str | None}
    """
    bpm = detect_bpm(path)
    key_result = detect_key(path)
    return {
        "bpm": bpm,
        "key_camelot": key_result[0] if key_result else None,
        "key_name": key_result[1] if key_result else None,
    }
