"""Freie Tags auf Tracks: Normalisierung (Issue #563).

Tags sind eigene Begriffe wie „Opener“ oder „Peak Time“. Damit „Peak Time“, „peak  time“
und „ PEAK TIME “ derselbe Tag sind, läuft jede Eingabe durch ``normalize_tag``, bevor sie
in ``track_tags`` landet oder dort gesucht wird (``TrackDB.add_track_tag`` u. a.).
"""

from __future__ import annotations

import re
import unicodedata

TAG_MAX_LEN = 40

# Bewusst eng: Tags sollen später in Dateinamen, Rekordbox-Kommentare und URLs passen,
# ohne dort maskiert werden zu müssen.
_ERLAUBT = re.compile(r"[a-z0-9äöüß _-]+")


def normalize_tag(raw: str) -> str:
    """Kleinschreibung, Leerraum zusammengefasst und am Rand entfernt.

    Erlaubt sind a-z, 0-9, ä ö ü ß, Leerzeichen, '-' und '_'.
    ValueError bei leerem Ergebnis, mehr als TAG_MAX_LEN Zeichen oder unerlaubtem Zeichen.
    """
    if not isinstance(raw, str):
        raise ValueError(f"Tag muss ein Text sein, nicht {type(raw).__name__}.")
    # NFC, damit ein zerlegtes „a + ¨“ (macOS-Dateinamen, Zwischenablage) als „ä“ gilt
    # und nicht als unerlaubtes Zeichen abgelehnt wird.
    tag = " ".join(unicodedata.normalize("NFC", raw).lower().split())
    if not tag:
        raise ValueError("Tag ist leer.")
    if len(tag) > TAG_MAX_LEN:
        raise ValueError(f"Tag ist länger als {TAG_MAX_LEN} Zeichen: {len(tag)}.")
    if not _ERLAUBT.fullmatch(tag):
        raise ValueError(
            f"Tag {tag!r} enthält unerlaubte Zeichen "
            "(erlaubt: a-z, 0-9, ä ö ü ß, Leerzeichen, '-' und '_')."
        )
    return tag
