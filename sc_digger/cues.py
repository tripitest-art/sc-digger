"""Cue-Tags in Audiodateien: First-Kick, Drop und Breakdown lesen und schreiben.

Der Tag `SCDIGGER_FIRST_KICK` liegt in der Datei (Sekunden) und wird vom Rekordbox-XML-Export
als Memory Cue übernommen. Ablage wie die Lautheits-Tags in organize.py: MP3/WAV/AIFF als
ID3-TXXX-Frame mit desc = Tagname, FLAC als Vorbis-Kommentar, M4A als Freeform. Audio bleibt
unangetastet (Goldene Regel 4). Dazu kommen `SCDIGGER_DROP` (Drop-Zeitpunkt),
`SCDIGGER_BREAKDOWN` (Beginn des Breakdowns) und `SCDIGGER_BREAKDOWN_KIND` (`silence` oder
`groove`); write_stem_cues schreibt die primären Cues aus einem StemAnalysisResult.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

from sc_digger.stems import StemAnalysisResult

log = logging.getLogger(__name__)

FIRST_KICK_TAG = "SCDIGGER_FIRST_KICK"
DROP_TAG = "SCDIGGER_DROP"
BREAKDOWN_TAG = "SCDIGGER_BREAKDOWN"
BREAKDOWN_KIND_TAG = "SCDIGGER_BREAKDOWN_KIND"

_ID3_EXTS = (".mp3", ".wav", ".aif", ".aiff")


def _valid_seconds(seconds: float) -> bool:
    """True, wenn seconds eine endliche Zahl >= 0 ist."""
    try:
        val = float(seconds)
    except (TypeError, ValueError):
        return False
    return math.isfinite(val) and val >= 0


def _m4a_freeform_key(tag: str) -> str:
    """Freeform-Schlüssel eines M4A-Tags im iTunes-Namensraum."""
    return f"----:com.apple.iTunes:{tag}"


def _write_string_tag(path: Path, tag: str, value: str) -> bool:
    """Schreibt value in den Tag tag im formatspezifischen Ablageort. True bei Erfolg.

    Ablage wie die Lautheits-Tags in organize.py: MP3/WAV/AIFF als ID3-TXXX-Frame mit
    desc = tag, FLAC als Vorbis-Kommentar, M4A als Freeform. False (kein Fehler) bei nicht
    existierender Datei, unbekanntem Format und jedem Schreibfehler. Wirft nie.
    """
    path = Path(path)
    try:
        if not path.is_file():
            log.warning("Datei existiert nicht für Tag %s: %s", tag, path)
            return False

        try:
            import mutagen
        except ImportError:
            log.warning("mutagen nicht installiert – Tag %s übersprungen", tag)
            return False

        ext = path.suffix.lower()

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

            tags.delall(f"TXXX:{tag}")
            tags.add(TXXX(encoding=3, desc=tag, text=[value]))
            tags.save(str(path))
            return True

        if ext == ".flac":
            from mutagen.flac import FLAC
            audio = FLAC(str(path))
            audio[tag] = value
            audio.save()
            return True

        if ext == ".m4a":
            from mutagen.mp4 import MP4
            audio = MP4(str(path))
            if audio.tags is None:
                audio.add_tags()
            key = _m4a_freeform_key(tag)
            if key in audio.tags:
                del audio.tags[key]
            audio[key] = [value.encode("utf-8")]
            audio.save()
            return True

        log.warning("Unbekanntes Format für Tag %s: %s", tag, path)
        return False

    except Exception as e:
        log.warning("Tag %s schreiben fehlgeschlagen für %s: %s", tag, path, e)
        return False


def _read_string_tag(path: Path, tag: str) -> str | None:
    """Liest den Tag tag als Rohstring. None, wenn er fehlt, die Datei nicht lesbar ist oder
    das Format keine Tags kennt. Wirft nie.
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
            frame = tags.get(f"TXXX:{tag}")
            if frame is not None and getattr(frame, "text", None):
                raw = str(frame.text[0])
        elif ext in (".wav", ".aif", ".aiff"):
            audio = mutagen.File(str(path))
            tags = audio.tags if audio is not None else None
            frame = tags.get(f"TXXX:{tag}") if tags is not None else None
            if frame is not None and getattr(frame, "text", None):
                raw = str(frame.text[0])
        elif ext == ".flac":
            from mutagen.flac import FLAC
            audio = FLAC(str(path))
            vals = audio.get(tag)
            if vals:
                raw = str(vals[0])
        elif ext == ".m4a":
            from mutagen.mp4 import MP4
            audio = MP4(str(path))
            vals = audio.get(_m4a_freeform_key(tag))
            if vals:
                first = vals[0]
                raw = first.decode("utf-8") if isinstance(first, bytes) else str(first)

        if raw is None:
            return None

        return raw

    except Exception:
        return None


def _parse_seconds(raw: str | None) -> float | None:
    """Rohstring als endliche Sekundenzahl >= 0, sonst None."""
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(val) or val < 0:
        return None
    return val


def write_first_kick_tag(path: Path, seconds: float) -> bool:
    """Schreibt seconds als f"{seconds:.3f}" in den Tag FIRST_KICK_TAG, ersetzt einen vorhandenen
    Wert (kein Doppel). True bei Erfolg. False (kein Fehler) bei nicht existierender Datei,
    unbekanntem Format, seconds < 0, NaN oder inf und bei jedem Schreibfehler. Wirft nie.
    """
    path = Path(path)
    if not path.is_file():
        log.warning("Datei existiert nicht für First-Kick-Tag: %s", path)
        return False
    if not _valid_seconds(seconds):
        log.warning("Ungültiger First-Kick-Wert für %s: %r", path, seconds)
        return False
    return _write_string_tag(path, FIRST_KICK_TAG, f"{float(seconds):.3f}")


def read_first_kick_tag(path: Path) -> float | None:
    """Liest den Tag und gibt Sekunden zurück. None, wenn er fehlt, kein endlicher Wert >= 0 ist,
    die Datei nicht lesbar ist oder das Format keine Tags kennt. Wirft nie.
    """
    return _parse_seconds(_read_string_tag(Path(path), FIRST_KICK_TAG))


def write_drop_tag(path: Path, seconds: float) -> bool:
    """Schreibt seconds als f"{seconds:.3f}" in den Tag DROP_TAG, ersetzt einen vorhandenen Wert
    (kein Doppel). True bei Erfolg. False (kein Fehler) bei nicht existierender Datei,
    unbekanntem Format, seconds < 0, NaN oder inf und bei jedem Schreibfehler. Wirft nie.
    """
    path = Path(path)
    if not path.is_file():
        log.warning("Datei existiert nicht für Drop-Tag: %s", path)
        return False
    if not _valid_seconds(seconds):
        log.warning("Ungültiger Drop-Wert für %s: %r", path, seconds)
        return False
    return _write_string_tag(path, DROP_TAG, f"{float(seconds):.3f}")


def read_drop_tag(path: Path) -> float | None:
    """Liest DROP_TAG als Sekunden. None, wenn er fehlt oder kein endlicher Wert >= 0 ist.
    Wirft nie.
    """
    return _parse_seconds(_read_string_tag(Path(path), DROP_TAG))


def write_breakdown_tag(path: Path, seconds: float, kind: str | None = None) -> bool:
    """Schreibt SCDIGGER_BREAKDOWN (seconds als f"{seconds:.3f}") und, wenn kind nicht None ist,
    SCDIGGER_BREAKDOWN_KIND ("silence" oder "groove"). True, wenn der Sekunden-Tag und ein
    angegebener kind-Tag geschrieben wurden. False (kein Fehler) bei nicht existierender Datei,
    unbekanntem Format, ungültigen Sekunden und jedem Schreibfehler. Wirft nie.
    """
    path = Path(path)
    if not path.is_file():
        log.warning("Datei existiert nicht für Breakdown-Tag: %s", path)
        return False
    if not _valid_seconds(seconds):
        log.warning("Ungültiger Breakdown-Wert für %s: %r", path, seconds)
        return False
    ok = _write_string_tag(path, BREAKDOWN_TAG, f"{float(seconds):.3f}")
    if kind is not None:
        ok = _write_string_tag(path, BREAKDOWN_KIND_TAG, str(kind)) and ok
    return ok


def read_breakdown_tag(path: Path) -> float | None:
    """Liest SCDIGGER_BREAKDOWN als Sekunden. None, wenn er fehlt oder kein endlicher Wert >= 0
    ist. Wirft nie.
    """
    return _parse_seconds(_read_string_tag(Path(path), BREAKDOWN_TAG))


def read_breakdown_kind_tag(path: Path) -> str | None:
    """Liest SCDIGGER_BREAKDOWN_KIND ("silence" oder "groove") oder None. Leere Werte und
    fehlende Tags ergeben None. Wirft nie.
    """
    raw = _read_string_tag(Path(path), BREAKDOWN_KIND_TAG)
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def write_stem_cues(path: Path, result: StemAnalysisResult) -> bool:
    """Schreibt first_kick_s, den ersten Drop und den ersten Breakdown aus result in path.

    Nutzt write_first_kick_tag, write_drop_tag und write_breakdown_tag. True, wenn alle
    tatsächlich geschriebenen Tags erfolgreich waren; False, wenn nichts zu schreiben war oder
    ein Schreibvorgang fehlschlägt. Wirft nie.
    """
    wrote = False
    ok = True
    if result.first_kick_s is not None:
        wrote = True
        ok = write_first_kick_tag(path, result.first_kick_s) and ok
    if result.drops:
        wrote = True
        ok = write_drop_tag(path, result.drops[0].start_s) and ok
    if result.breakdowns:
        wrote = True
        first = result.breakdowns[0]
        ok = write_breakdown_tag(path, first.start_s, first.kind) and ok
    return wrote and ok
