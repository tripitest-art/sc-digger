"""Ähnlichkeitssuche über Chromaprint-Fingerprints: „Mehr davon“ im Telegram-Bot.

Warum ein eigenes Modul: Der Bot soll zu einem Fingerprint die ähnlichsten bereits
analysierten Aufnahmen aus der Track-DB nennen (Bot-Antwort, keine Downloads). Die
Vergleichsmathematik bleibt in `sc_digger.fingerprint`, hier steht nur die Auswahl
gegen die Datenbank und die Formatierung des Bot-Textes.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Iterable

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


@dataclass(frozen=True)
class SimilarCandidate:
    """Ein Kandidat der reinen Ähnlichkeitssuche: Track plus Chromaprint-Rohwerte."""

    track: "TrackRecord"
    fingerprint: list[int]      # Rohwerte des Chromaprint (32-Bit-Wörter)


@dataclass(frozen=True)
class SimilarResult:
    """Ein Treffer der Ähnlichkeitssuche samt Bitfehlerrate (0..1)."""

    track: "TrackRecord"
    ber: float


def find_similar(
    query: list[int],
    candidates: Iterable[SimilarCandidate],
    *,
    top_n: int = 5,
) -> list[SimilarResult]:
    """Vergleicht query per Bitfehlerrate (BER, Anteil abweichender Bits, 0..1) mit jedem
    Kandidaten – identisch zur Berechnung in `fingerprint.bit_error_rate` (die Funktion wird
    wiederverwendet). Kandidaten mit BER <= MAX_BER (der Track selbst bzw. Duplikate) fallen
    raus. Die übrigen kommen aufsteigend nach BER zurück; bei gleicher BER bleibt die
    Reihenfolge der Kandidaten erhalten. Zurück kommen die ersten top_n. Gibt es nach dem
    Ausschluss keine Kandidaten (oder ist top_n <= 0), kommt [] zurück, kein Fehler.
    """
    if top_n <= 0:
        return []

    results = [
        SimilarResult(candidate.track, bit_error_rate(query, candidate.fingerprint))
        for candidate in candidates
    ]
    results = [result for result in results if result.ber > MAX_BER]
    results.sort(key=lambda result: result.ber)
    return results[:top_n]


def format_similar_list(results: list[SimilarResult]) -> str:
    """Bot-Text mit den übergebenen Treffern.

    Erste Zeile ist '🎧 Ähnliche Aufnahmen:', danach je Ergebnis eine Zeile
    '1. {artist} – {title} · {bpm} BPM · {key} · Ähnlichkeit {s:.1f}%' (Rang ab 1;
    bpm/key als '–' wenn None/fehlend, ebenso artist/title; s = (1 - ber) * 100).
    Leere Liste -> genau `NO_SIMILAR`.
    """
    if not results:
        return NO_SIMILAR

    lines = ["🎧 Ähnliche Aufnahmen:"]
    for rank, result in enumerate(results, start=1):
        rec = result.track
        artist = rec.artist or "–"
        title = rec.title or "–"
        bpm = "–" if rec.bpm is None else f"{rec.bpm}"
        key = rec.key_camelot or "–"
        lines.append(
            f"{rank}. {artist} – {title} · {bpm} BPM · {key}"
            f" · Ähnlichkeit {(1.0 - result.ber) * 100:.1f}%"
        )
    return "\n".join(lines)
