"""Ähnlichkeitssuche über Chromaprint-Fingerprints: „Mehr davon“ im Telegram-Bot.

Warum ein eigenes Modul: Der Bot soll zu einem Fingerprint die ähnlichsten bereits
analysierten Aufnahmen aus der Track-DB nennen (Bot-Antwort, keine Downloads). Die
Vergleichsmathematik bleibt in `sc_digger.fingerprint`, hier steht nur die Auswahl
gegen die Datenbank und die Formatierung des Bot-Textes.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .fingerprint import MAX_BER, bit_error_rate, decode_fingerprint

if TYPE_CHECKING:
    from .db import TrackDB, TrackRecord

log = logging.getLogger("sc_digger.similarity")

NO_SIMILAR = "Keine ähnlichen Aufnahmen gefunden."
SIMILAR_FAILED = "Ähnliche-Aufnahmen-Suche fehlgeschlagen, siehe Container-Log."


def build_similar_reply(db: "TrackDB", query_fp: list[int], *, top_n: int = 5) -> str:
    """Bot-Text mit den bis zu top_n ähnlichsten Aufnahmen zu query_fp.

    Kandidaten sind alle Tracks mit Fingerprint (`db.tracks_with_fingerprint()`, ohne
    Änderung an der DB). Verglichen wird per Bitfehlerrate (`fingerprint.bit_error_rate`);
    Kandidaten mit BER <= `fingerprint.MAX_BER` sind dieselbe Aufnahme bzw. Duplikate und
    fliegen raus. Sortiert aufsteigend nach BER, dann top_n ausgeben. Gibt es nichts,
    kommt `NO_SIMILAR`. Fehler (z. B. DB nicht lesbar) werden geloggt; Rückgabe dann
    `SIMILAR_FAILED`. Wirft nie.
    """
    try:
        scored: list[tuple[float, "TrackRecord"]] = []
        for rec in db.tracks_with_fingerprint():
            values = decode_fingerprint(rec.fingerprint or "")
            if not values:
                continue
            ber = bit_error_rate(query_fp, values)
            if ber <= MAX_BER:
                continue
            scored.append((ber, rec))
        scored.sort(key=lambda item: item[0])
        return _format_similar(scored[:top_n])
    except Exception:
        log.exception("Ähnliche-Aufnahmen-Suche fehlgeschlagen")
        return SIMILAR_FAILED


def _format_similar(scored: list[tuple[float, "TrackRecord"]]) -> str:
    """Zeilen für die Bot-Antwort: Kopfzeile plus je Ergebnis Rang, Track und Ähnlichkeit."""
    if not scored:
        return NO_SIMILAR
    lines = ["🎧 Ähnliche Aufnahmen:"]
    for rank, (ber, rec) in enumerate(scored, start=1):
        artist = rec.artist or "?"
        title = rec.title or rec.path
        bpm = f"{rec.bpm:.1f}" if rec.bpm is not None else "–"
        key = rec.key_camelot or "–"
        lines.append(
            f"{rank}. {artist} – {title} · {bpm} BPM · {key} · Ähnlichkeit {(1.0 - ber) * 100:.1f}%"
        )
    return "\n".join(lines)
