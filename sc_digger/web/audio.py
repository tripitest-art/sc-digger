"""Vorhören im Track-Explorer: Pfadprüfung und Range-Header (Issue #350).

Der Browser bekommt Audiodateien nur aus den konfigurierten Ordnern (Inbox und Sammlung)
ausgeliefert. Die Prüfung löst Symlinks und ``..`` vor dem Vergleich auf, damit ein Pfad
in der Track-DB nicht aus den erlaubten Wurzeln ausbrechen kann. Gelesen wird nur.
"""
from __future__ import annotations

from pathlib import Path

PLAYABLE_SUFFIXES: frozenset[str] = frozenset(
    {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".ogg", ".opus"}
)


def resolve_playable_path(track_path: str, roots: list[Path]) -> Path | None:
    """Aufgelöster Pfad, wenn die Datei existiert, abspielbar ist und unter ``roots`` liegt.

    Symlinks und ``..`` zählen nach dem Auflösen: eine Verknüpfung in der Inbox auf eine
    Datei außerhalb wird abgelehnt. Fehlende Wurzeln und nicht auflösbare Pfade ergeben
    ``None``. Die Funktion wirft nie.
    """
    try:
        if not track_path:
            return None
        path = Path(track_path).resolve()
        if path.suffix.lower() not in PLAYABLE_SUFFIXES:
            return None
        if not path.is_file():
            return None
        for root in roots:
            try:
                if path.is_relative_to(Path(root).resolve()):
                    return path
            except (OSError, RuntimeError):
                continue
    except (OSError, RuntimeError, ValueError):
        return None
    return None


def parse_range_header(header: str | None, size: int) -> tuple[int, int] | None:
    """Range-Header als inklusives ``(start, end)``; ``None`` heißt ganze Datei.

    Unterstützt ``bytes=a-b``, ``bytes=a-`` und ``bytes=-n``. Ungültige, nicht erfüllbare
    oder mehrere Bereiche ergeben ``ValueError`` (die Route antwortet dann mit 416).
    """
    if header is None:
        return None
    value = header.strip()
    if not value.lower().startswith("bytes="):
        raise ValueError("Nur die Einheit bytes wird unterstützt")
    spec = value[len("bytes="):].strip()
    if "," in spec:
        raise ValueError("Mehrfach-Bereiche werden nicht unterstützt")
    start_text, separator, end_text = spec.partition("-")
    if not separator:
        raise ValueError("Bereich ohne Bindestrich")
    start_text = start_text.strip()
    end_text = end_text.strip()

    if not start_text:
        # Suffix-Bereich: die letzten n Bytes.
        if not end_text.isdigit():
            raise ValueError("Ungültiger Suffix-Bereich")
        suffix = int(end_text)
        if suffix <= 0 or size <= 0:
            raise ValueError("Bereich nicht erfüllbar")
        return max(0, size - suffix), size - 1

    if not start_text.isdigit():
        raise ValueError("Ungültiger Start")
    start = int(start_text)
    if start >= size:
        raise ValueError("Start liegt hinter dem Dateiende")
    if not end_text:
        return start, size - 1
    if not end_text.isdigit():
        raise ValueError("Ungültiges Ende")
    end = int(end_text)
    if start > end:
        raise ValueError("Start liegt hinter dem Ende")
    return start, min(end, size - 1)
