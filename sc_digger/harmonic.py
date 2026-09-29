"""Harmonic Mixing: kompatible Camelot-Keys und passende Tracks aus der Track-DB."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .db import TrackDB, TrackRecord

_CAMELOT_RE = re.compile(r"^([1-9]|1[0-2])([AB])$")


def compatible_keys(key: str) -> list[str]:
    """Kompatible Camelot-Keys in fester Reihenfolge: [gleicher Key, eins runter, eins hoch,
    gleiche Zahl mit anderem Buchstaben]. Das Rad schließt sich (1 runter -> 12, 12 hoch -> 1).
    Eingabe ohne Rücksicht auf Groß/klein und Leerzeichen am Rand ("5a", " 8B ").
    Ausgabe immer normalisiert ("5A"). Ungültig (leer, 0, 13, andere Buchstaben, "Am") -> ValueError."""
    if not isinstance(key, str):
        raise ValueError(f"Ungültiger Camelot-Key: {key!r}")
    normalized = key.strip().upper()
    m = _CAMELOT_RE.match(normalized)
    if not m:
        raise ValueError(f"Ungültiger Camelot-Key: {key!r}")

    num = int(m.group(1))
    letter = m.group(2)

    down_num = 12 if num == 1 else num - 1
    up_num = 1 if num == 12 else num + 1
    other_letter = "B" if letter == "A" else "A"

    return [
        f"{num}{letter}",
        f"{down_num}{letter}",
        f"{up_num}{letter}",
        f"{num}{other_letter}",
    ]


def find_mix_candidates(
    db: TrackDB,
    key: str,
    bpm: float,
    *,
    bpm_tolerance: float = 3.0,
    limit: int = 30,
) -> list[TrackRecord]:
    """Tracks aus der Tabelle tracks mit key_camelot in compatible_keys(key) und
    |bpm - Ziel| <= bpm_tolerance (Grenze einschließlich). Ausgeschlossen: bpm oder key_camelot NULL,
    status 'rejected', quality_status 'fake_transcode', 'clipped', 'corrupt'.
    Sortierung: Position des Keys in compatible_keys, dann |BPM-Abstand|, dann artist, title, path.
    Höchstens limit Einträge. Nur lesend. Ungültiger Key -> ValueError (aus compatible_keys).
    SQL direkt über db.db in diesem Modul (db.py wird gerade in PR #71 geändert, Regel 8)."""
    comp_keys = compatible_keys(key)
    from .db import TrackRecord

    query = """
        SELECT * FROM tracks
        WHERE key_camelot IN (?, ?, ?, ?)
          AND bpm IS NOT NULL
          AND key_camelot IS NOT NULL
          AND abs(bpm - ?) <= ?
          AND (status IS NULL OR status != 'rejected')
          AND (quality_status IS NULL OR quality_status NOT IN ('fake_transcode', 'clipped', 'corrupt'))
        ORDER BY
            CASE key_camelot
                WHEN ? THEN 0
                WHEN ? THEN 1
                WHEN ? THEN 2
                WHEN ? THEN 3
                ELSE 4
            END ASC,
            abs(bpm - ?) ASC,
            COALESCE(artist, '') ASC,
            COALESCE(title, '') ASC,
            path ASC
        LIMIT ?
    """
    params = (
        comp_keys[0],
        comp_keys[1],
        comp_keys[2],
        comp_keys[3],
        float(bpm),
        float(bpm_tolerance),
        comp_keys[0],
        comp_keys[1],
        comp_keys[2],
        comp_keys[3],
        float(bpm),
        int(limit),
    )
    rows = db.db.execute(query, params).fetchall()
    return [TrackRecord.from_row(r) for r in rows]


def format_mix_list(
    key: str,
    bpm: float,
    records: list[TrackRecord],
    *,
    bpm_tolerance: float = 3.0,
) -> str:
    """Klartext (kein HTML) für Telegram/Konsole.
    Kopf: "🎛 <KEY> · <bpm:g> BPM ±<bpm_tolerance:g>: <n> Track" bzw. "... Tracks" ab 2;
          bei 0 Einträgen "🎛 <KEY> · <bpm:g> BPM ±<tol:g>: keine passenden Tracks".
    Je Eintrag eine Zeile: "<artist or '?'> – <title or path> · <bpm:.1f> BPM · <key_camelot>"."""
    try:
        norm_key = compatible_keys(key)[0]
    except ValueError:
        norm_key = key.strip().upper()

    if not records:
        return f"🎛 {norm_key} · {bpm:g} BPM ±{bpm_tolerance:g}: keine passenden Tracks"

    count_str = "1 Track" if len(records) == 1 else f"{len(records)} Tracks"
    header = f"🎛 {norm_key} · {bpm:g} BPM ±{bpm_tolerance:g}: {count_str}"
    lines = [header]
    for r in records:
        artist = r.artist or "?"
        title = r.title or r.path
        bpm_str = f"{r.bpm:.1f}" if r.bpm is not None else "??"
        key_str = r.key_camelot or "?"
        lines.append(f"{artist} – {title} · {bpm_str} BPM · {key_str}")

    return "\n".join(lines)
