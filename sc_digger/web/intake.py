"""Web-Upload in den Eingangsordner (Issue #230).

Stephan lädt Dateien, die er nach einem Gate (Hypeddit, Droploud, …) manuell am PC geholt hat,
per Drag & Drop im Dashboard hoch. ``process_uploaded_audio`` legt sie in den Eingangsordner und
schickt sie durch ``main.finish_file`` – denselben Weg wie ``main.run_intake``: Format- und
Fake-Prüfung, BPM/Key-Analyse, Tagging und Sortierung in die Inbox (oder nach ``_rejected``).
Gates werden dabei nicht umgangen (Regel 6); der Upload ersetzt nur das manuelle Kopieren.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ..intake import AUDIO_EXTS, track_from_file
from ..models import Config, Track
from ..redact import redact

log = logging.getLogger(__name__)

# In Stücken lesen und schreiben: Originale können mehrere hundert MB groß sein (Server: 1 GB RAM).
_CHUNK_SIZE = 1024 * 1024

# Obergrenze für einen Upload (Originale: WAV/AIFF bis wenige hundert MB). Ohne Limit schreibt
# ``_copy_stream`` bis die Platte voll ist, nachdem Starlette den Body schon einmal gespoolt hat.
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024

# Feste Meldungen für den Browser: Ausnahmetexte enthalten Containerpfade (/music/inbox, /data) und
# ffmpeg-Ausgaben. Die Ursache steht (mit ``redact``) im Log.
MSG_UPLOAD_FAILED = "Upload fehlgeschlagen, Details im Log"
MSG_PROCESSING_FAILED = "Verarbeitung fehlgeschlagen, Details im Log"
MSG_CHECK_FAILED = "Prüfung fehlgeschlagen, Details im Log"


class UploadTooLarge(Exception):
    """Der Upload überschreitet ``MAX_UPLOAD_BYTES``."""


@dataclass
class UploadResult:
    ok: bool
    filename: str
    status: str  # 'inbox', 'rejected', 'error', 'too_large'
    message: str
    dest_path: str | None = None
    bpm: float | None = None
    key_camelot: str | None = None


def process_uploaded_audio(
    file_bytes: BinaryIO,
    filename: str,
    cfg: Config,
    max_bytes: int | None = None,
) -> UploadResult:
    """Nimmt einen Audio-Upload entgegen, speichert die Datei temporär im intake_dir,
    prüft Format und Fake-Status via finish_file(), analysiert BPM/Key und sortiert
    die Datei in die Inbox ein (oder nach _rejected bei Fake).
    Gibt ein strukturiertes UploadResult zurück. Wirft nie unkontrolliert.
    """
    # Nur der reine Dateiname: „../../etc/x.wav“ darf den Eingangsordner nicht verlassen.
    limit = MAX_UPLOAD_BYTES if max_bytes is None else max_bytes
    name = Path(str(filename or "")).name.strip()
    if not name or Path(name).suffix.lower() not in AUDIO_EXTS:
        return UploadResult(
            ok=False,
            filename=name or str(filename or ""),
            status="error",
            message=f"Kein Audio-Format: {name or 'Datei ohne Namen'}",
        )
    try:
        return _ingest(file_bytes, name, cfg, limit)
    except UploadTooLarge:
        log.warning("Upload von %s abgelehnt: größer als %d MB", name, limit // (1024 * 1024))
        return UploadResult(
            ok=False, filename=name, status="too_large",
            message=f"Datei zu groß (max. {limit // (1024 * 1024)} MB)",
        )
    except Exception as e:
        # Vertrag der Route: auch ein unerwarteter Fehler kommt als UploadResult zurück (Regel 7
        # bleibt gewahrt, der Aufrufer sieht die Ursache im Feld message).
        log.warning("Upload von %s fehlgeschlagen: %s", name, redact(e))
        return UploadResult(ok=False, filename=name, status="error", message=MSG_UPLOAD_FAILED)


def _ingest(file_bytes: BinaryIO, name: str, cfg: Config, max_bytes: int) -> UploadResult:
    """Speichert den Upload im Eingangsordner und verarbeitet ihn über ``finish_file``."""
    download = cfg.raw.get("download") or {}
    intake_dir_str = download.get("intake_dir") or ""
    inbox_str = download.get("inbox_dir") or ""
    if not intake_dir_str:
        return UploadResult(ok=False, filename=name, status="error",
                            message="Kein Eingangsordner konfiguriert (download.intake_dir)")
    if not inbox_str:
        return UploadResult(ok=False, filename=name, status="error",
                            message="Keine Inbox konfiguriert (download.inbox_dir)")

    intake_dir = Path(intake_dir_str)
    inbox = Path(inbox_str)
    intake_dir.mkdir(parents=True, exist_ok=True)

    # Erst unter verstecktem Namen schreiben (find_ready_files überspringt „.“-Dateien), dann
    # atomar umbenennen: ein abgebrochener Upload darf nie als fertige Datei gelten.
    target = _free_path(intake_dir, name)
    staging = intake_dir / f".{target.name}.part"
    try:
        with open(staging, "wb") as handle:
            _copy_stream(file_bytes, handle, max_bytes)
        os.replace(staging, target)
    except BaseException:
        _remove(staging)
        raise

    t = track_from_file(target)
    try:
        # Import erst hier: die Web-App muss die restliche Pipeline nicht mitladen.
        from ..main import finish_file

        result_path = finish_file(t, target, inbox, cfg)
    except Exception as e:
        _remove(target)
        log.warning("Verarbeitung von %s fehlgeschlagen: %s", name, redact(e))
        return UploadResult(ok=False, filename=name, status="error",
                            message=MSG_PROCESSING_FAILED)

    if result_path is not None:
        bpm = f"{t.bpm:.0f}" if t.bpm else "?"
        display = f"{t.artist} – {t.title}" if t.artist else t.title
        message = f"✅ {display} · {bpm} BPM · {t.key_camelot or '?'} · in der Inbox"
        return UploadResult(ok=True, filename=name, status="inbox", message=message,
                            dest_path=str(result_path), bpm=t.bpm, key_camelot=t.key_camelot)

    # finish_file gibt None: entweder scheiterte die Prüfung (Datei liegt noch) oder die Datei
    # wurde aussortiert (Fake, Brickwall, Duplikat).
    if target.exists():
        fehler_dir = intake_dir / "_fehler"
        fehler_dir.mkdir(parents=True, exist_ok=True)
        moved = fehler_dir / target.name
        try:
            os.replace(target, moved)
            dest_path = str(moved)
        except OSError as e:
            log.warning("Datei konnte nicht nach _fehler verschoben werden: %s", e)
            dest_path = str(target)
        # Die letzte Notiz kann einen Ausnahmetext mit Pfaden enthalten (z. B. „Audio-Analyse
        # fehlgeschlagen: …“): nur geschwärzt ins Log, der Browser bekommt die feste Meldung.
        if t.notes:
            log.warning("Prüfung von %s fehlgeschlagen: %s", name, redact(t.notes[-1]))
        return UploadResult(ok=False, filename=name, status="error", message=MSG_CHECK_FAILED,
                            dest_path=dest_path)

    if t.duplicate_of:
        return UploadResult(ok=False, filename=name, status="rejected",
                            message="schon in der Sammlung",
                            dest_path=_rejected_path(inbox, target.name))
    return UploadResult(ok=False, filename=name, status="rejected",
                        message=_reject_reason(t),
                        dest_path=_rejected_path(inbox, target.name))


def _copy_stream(src: BinaryIO, dst: BinaryIO, max_bytes: int) -> None:
    """Kopiert den Stream blockweise, ohne die ganze Datei in den Speicher zu laden.

    Zählt mit und bricht bei mehr als ``max_bytes`` mit ``UploadTooLarge`` ab (der Aufrufer
    räumt die Teil-Datei weg). Der Content-Length-Header allein genügt nicht: er fehlt bei
    Chunked-Uploads und kann falsch sein.
    """
    total = 0
    while True:
        chunk = src.read(_CHUNK_SIZE)
        if not chunk:
            return
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLarge
        dst.write(chunk)


def _free_path(directory: Path, name: str) -> Path:
    """Freien Dateinamen im Ordner finden; eine vorhandene Datei wird nie überschrieben."""
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, suffix = Path(name).stem, Path(name).suffix
    number = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{number}{suffix}"
        number += 1
    return candidate


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _reject_reason(t: Track) -> str:
    """Ablehnungsgrund wie in ``main.run_intake``."""
    if t.quality_report and t.quality_report.get("clipped"):
        return "Brickwall-Master (Clipping)"
    if t.quality_report and t.quality_report.get("reason"):
        return str(t.quality_report["reason"])
    return "abgelehnt"


def _rejected_path(inbox: Path, name: str) -> str | None:
    """Wohin ``finalize_quality`` die Datei gelegt hat (_rejected, /clipped oder /duplicate)."""
    for sub in ("", "clipped", "duplicate"):
        candidate = inbox / "_rejected" / sub / name if sub else inbox / "_rejected" / name
        if candidate.exists():
            return str(candidate)
    return None
