"""Cue-Tags in Audiodateien: First-Kick-Position lesen und schreiben.

Der Tag `SCDIGGER_FIRST_KICK` liegt in der Datei (Sekunden) und wird vom Rekordbox-XML-Export
als Memory Cue übernommen. Ablage wie die Lautheits-Tags in organize.py: MP3/WAV/AIFF als
ID3-TXXX-Frame mit desc = Tagname, FLAC als Vorbis-Kommentar, M4A als Freeform. Audio bleibt
unangetastet (Goldene Regel 4). Wer den Tag befüllt, kommt in einer Folgeaufgabe.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

log = logging.getLogger(__name__)

FIRST_KICK_TAG = "SCDIGGER_FIRST_KICK"

_M4A_FREEFORM_KEY = f"----:com.apple.iTunes:{FIRST_KICK_TAG}"

_ID3_EXTS = (".mp3", ".wav", ".aif", ".aiff")


def _valid_seconds(seconds: float) -> bool:
    """True, wenn seconds eine endliche Zahl >= 0 ist."""
    try:
        val = float(seconds)
    except (TypeError, ValueError):
        return False
    return math.isfinite(val) and val >= 0


def write_first_kick_tag(path: Path, seconds: float) -> bool:
    """Schreibt seconds als f"{seconds:.3f}" in den Tag FIRST_KICK_TAG, ersetzt einen vorhandenen
    Wert (kein Doppel). True bei Erfolg. False (kein Fehler) bei nicht existierender Datei,
    unbekanntem Format, seconds < 0, NaN oder inf und bei jedem Schreibfehler. Wirft nie.
    """
    path = Path(path)
    try:
        if not path.is_file():
            log.warning("Datei existiert nicht für First-Kick-Tag: %s", path)
            return False

        if not _valid_seconds(seconds):
            log.warning("Ungültiger First-Kick-Wert für %s: %r", path, seconds)
            return False

        value = f"{float(seconds):.3f}"
        ext = path.suffix.lower()

        try:
            import mutagen
        except ImportError:
            log.warning("mutagen nicht installiert – First-Kick-Tag übersprungen")
            return False

        if ext in _ID3_EXTS:
            from mutagen.id3 import TXXX
            if ext == ".mp3":
                from mutagen.id3 import ID3, ID3NoHeaderError
                try:
                    tags = ID3(str(path))
                except ID3NoHeaderError:
                    tags = ID3()
            elif ext == ".wav":
                from mutagen.wave import WAVE
                audio = WAVE(str(path))
                if audio.tags is None:
                    audio.add_tags()
                tags = audio.tags
            else:
                from mutagen.aiff import AIFF
                audio = AIFF(str(path))
                if audio.tags is None:
                    audio.add_tags()
                tags = audio.tags

            tags.delall(f"TXXX:{FIRST_KICK_TAG}")
            tags.add(TXXX(encoding=3, desc=FIRST_KICK_TAG, text=[value]))
            tags.save(str(path))
            return True

        if ext == ".flac":
            from mutagen.flac import FLAC
            audio = FLAC(str(path))
            audio[FIRST_KICK_TAG] = value
            audio.save()
            return True

        if ext == ".m4a":
            from mutagen.mp4 import MP4
            audio = MP4(str(path))
            if audio.tags is None:
                audio.add_tags()
            if _M4A_FREEFORM_KEY in audio.tags:
                del audio.tags[_M4A_FREEFORM_KEY]
            audio[_M4A_FREEFORM_KEY] = [value.encode("utf-8")]
            audio.save()
            return True

        log.warning("Unbekanntes Format für First-Kick-Tag: %s", path)
        return False

    except Exception as e:
        log.warning("First-Kick-Tag schreiben fehlgeschlagen für %s: %s", path, e)
        return False


def read_first_kick_tag(path: Path) -> float | None:
    """Liest den Tag und gibt Sekunden zurück. None, wenn er fehlt, kein endlicher Wert >= 0 ist,
    die Datei nicht lesbar ist oder das Format keine Tags kennt. Wirft nie.
    """
    path = Path(path)
    try:
        if not path.is_file():
            return None

        try:
            import mutagen
        except ImportError:
            return None

        ext = path.suffix.lower()

        raw: str | None = None
        if ext == ".mp3":
            from mutagen.id3 import ID3
            tags = ID3(str(path))
            frame = tags.get(f"TXXX:{FIRST_KICK_TAG}")
            if frame is not None and getattr(frame, "text", None):
                raw = str(frame.text[0])
        elif ext in (".wav", ".aif", ".aiff"):
            audio = mutagen.File(str(path))
            tags = audio.tags if audio is not None else None
            frame = tags.get(f"TXXX:{FIRST_KICK_TAG}") if tags is not None else None
            if frame is not None and getattr(frame, "text", None):
                raw = str(frame.text[0])
        elif ext == ".flac":
            from mutagen.flac import FLAC
            audio = FLAC(str(path))
            vals = audio.get(FIRST_KICK_TAG)
            if vals:
                raw = str(vals[0])
        elif ext == ".m4a":
            from mutagen.mp4 import MP4
            audio = MP4(str(path))
            vals = audio.get(_M4A_FREEFORM_KEY)
            if vals:
                first = vals[0]
                raw = first.decode("utf-8") if isinstance(first, bytes) else str(first)

        if raw is None:
            return None

        try:
            val = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(val) or val < 0:
            return None
        return val

    except Exception:
        return None
