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


def _from_filename(stem: str) -> tuple[str, str]:
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", stem


def track_from_file(path: Path) -> Track:
    """Track für eine manuell geladene Datei. artist/title aus den Tags (mutagen, easy=True),
    sonst aus dem Dateinamen "<Artist> - <Titel>" (am ersten " - " getrennt), sonst
    artist "" und title = Dateiname ohne Endung. id=0, url="", bpm=None, restliche Felder leer/0.
    Nicht lesbare Tags -> Dateiname, nie eine Exception.
    """
    path = Path(path)
    artist = ""
    title = ""

    try:
        from mutagen import File
        audio = File(path, easy=True)
        if audio:
            artists = audio.get("artist") or audio.get("TPE1") or []
            titles = audio.get("title") or audio.get("TIT2") or []
            if hasattr(artists, "text"):
                artists = artists.text
            if hasattr(titles, "text"):
                titles = titles.text
            if isinstance(artists, (list, tuple)) and artists:
                artist = str(artists[0]).strip()
            elif artists:
                artist = str(artists).strip()
            if isinstance(titles, (list, tuple)) and titles:
                title = str(titles[0]).strip()
            elif titles:
                title = str(titles).strip()
    except Exception:
        pass

    if not artist and not title:
        artist, title = _from_filename(path.stem)
    elif not title:
        title = path.stem

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
