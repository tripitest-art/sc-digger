"""Eingangsordner: manuell geladene Tracks finden und als Track-Objekt vorbereiten.

Dateien, die z. B. nach einem Gate-Download in den Eingangsordner (_eingang) gelegt werden,
durchlaufen dieselbe Pipeline wie native SoundCloud-Originale: Qualitätsprüfung, BPM/Key-Analyse,
Tagging und Sortierung in die Inbox.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from .models import Track

log = logging.getLogger(__name__)

# Unterstützte Audio-Endungen (gleiche Menge wie organize.write_tags)
AUDIO_EXTS = {".wav", ".aiff", ".aif", ".flac", ".mp3", ".m4a"}


def find_ready_files(intake_dir: str | Path, min_age_s: float = 120,
                     now: float | None = None) -> list[Path]:
    """Gibt alle Dateien in *intake_dir* zurück, die alt genug sind.

    - Sortiert alphabetisch nach relativem Pfad (as_posix).
    - Übersprungen: Dateien jünger als *min_age_s*, versteckte (beginnen mit „."),
      und alles in Unterordnern, deren Name mit „_" beginnt (z. B. _unbekannt, _fehler).
    - Nicht existierender *intake_dir* → leere Liste, kein Fehler.
    """
    intake_dir = Path(intake_dir)
    if not intake_dir.exists():
        return []

    now = now or time.time()
    result: list[Path] = []

    for root, dirs, files in os.walk(intake_dir):
        root_path = Path(root)
        # Unterordner mit „_" am Anfang komplett überspringen
        if root_path != intake_dir:
            try:
                rel = root_path.relative_to(intake_dir)
            except ValueError:
                continue
            if any(part.startswith("_") for part in rel.parts):
                continue
        # Unterordner prunen, die mit „_" beginnen
        dirs[:] = [d for d in dirs if not d.startswith("_") and not d.startswith(".")]

        for fname in sorted(files):
            if fname.startswith("."):
                continue
            fpath = root_path / fname
            mtime = fpath.stat().st_mtime
            if now - mtime < min_age_s:
                continue
            result.append(fpath)

    # Nach relativem Pfad sortiert
    result.sort(key=lambda p: p.relative_to(intake_dir).as_posix())
    return result


def track_from_file(path: Path) -> Track:
    """Erzeugt einen minimalen Track aus einer lokalen Datei.

    Artist und Title werden aus dem Dateinamen gelesen (Schema „Artist - Title.ext").
    Fehlt der Bindestrich, ist artist leer und title der Dateiname ohne Endung.
    Es wird KEIN mutagen-Lesen versucht: die Tags werden nach Analyse + Prüfung ohnehin
    überschrieben, und manche Gate-Downloads haben irreführende Tags.
    """
    stem = path.stem
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        artist = artist.strip()
        title = title.strip()
    else:
        artist = ""
        title = stem

    return Track(
        id=0,
        title=title,
        url="",
        artist=artist,
        artist_url="",
        created_at="",
        duration_ms=0,
        genre="",
        tags=[],
        description="",
        bpm=None,
        plays=0,
        likes=0,
        reposts=0,
        comments=0,
        downloadable=False,
        has_downloads_left=False,
        purchase_url=None,
        purchase_title=None,
    )
