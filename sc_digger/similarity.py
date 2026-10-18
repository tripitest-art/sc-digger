"""Ähnlichkeitssuche über Chromaprint-Fingerprints: „Mehr davon“ im Telegram-Bot.

Warum ein eigenes Modul: Der Bot soll zu einem Fingerprint die ähnlichsten bereits
analysierten Aufnahmen aus der Track-DB nennen (Bot-Antwort, keine Downloads). Die
Vergleichsmathematik bleibt in `sc_digger.fingerprint`, hier steht nur die Auswahl
gegen die Datenbank und die Formatierung des Bot-Textes.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Iterable, Sequence

from .fingerprint import MAX_BER, bit_error_rate

if TYPE_CHECKING:
    from .db import TrackDB, TrackRecord

log = logging.getLogger("sc_digger.similarity")

NO_SIMILAR = "Keine ähnlichen Aufnahmen gefunden."
SIMILAR_FAILED = "Ähnliche-Aufnahmen-Suche fehlgeschlagen, siehe Container-Log."
MIN_EMBEDDING_SCORE: float = 0.0


def build_similar_reply(db: "TrackDB", query_fp: list[int], *, top_n: int = 5) -> str:
    """Bot-Text mit den bis zu top_n ähnlichsten Aufnahmen zu query_fp.

    Kandidaten sind alle Tracks mit Fingerprint als Rohwerte
    (`db.fingerprinted_tracks()`, ohne Änderung an der DB). Verglichen und ausgewählt wird
    mit `find_similar` (Kandidaten mit BER <= `fingerprint.MAX_BER` sind dieselbe Aufnahme
    bzw. Duplikate und fliegen raus), formatiert mit `format_similar_list`. Gibt es nichts,
    kommt `NO_SIMILAR`. Fehler (z. B. DB nicht lesbar) werden geloggt; Rückgabe dann
    `SIMILAR_FAILED`. Wirft nie.
    """
    try:
        candidates = [
            SimilarCandidate(rec, values)
            for rec, values in db.fingerprinted_tracks()
            if values
        ]
        return format_similar_list(find_similar(query_fp, candidates, top_n=top_n))
    except Exception:
        log.exception("Ähnliche-Aufnahmen-Suche fehlgeschlagen")
        return SIMILAR_FAILED


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


@dataclass(frozen=True)
class EmbeddingCandidate:
    """Ein Kandidat der Embedding-Ähnlichkeitssuche: Track plus Merkmalsvektor."""

    track: "TrackRecord"
    vector: list[float]


@dataclass(frozen=True)
class EmbeddingResult:
    """Ein Treffer der Embedding-Ähnlichkeitssuche samt Kosinus-Ähnlichkeit (-1.0..1.0)."""

    track: "TrackRecord"
    score: float          # Cosine-Ähnlichkeit, -1.0..1.0


def find_similar_embeddings(
    query: Sequence[float],
    candidates: Iterable[EmbeddingCandidate],
    *,
    top_n: int = 5,
    exclude_track_id: int | None = None,
    min_score: float = MIN_EMBEDDING_SCORE,
) -> list[EmbeddingResult]:
    """Findet die ähnlichsten Kandidaten zu einem Query-Vektor per Kosinus-Ähnlichkeit.

    Kandidaten mit abweichender Vektorlänge werden übersprungen.
    Wird `exclude_track_id` übergeben, wird der Track mit dieser ID ignoriert.
    Kandidaten mit Score < min_score werden ignoriert.
    Ergebnisse werden absteigend nach Score sortiert (stabile Sortierung).
    Gibt maximal `top_n` Ergebnisse zurück (bei top_n <= 0 leere Liste).
    """
    if top_n <= 0:
        return []

    from .embeddings import cosine_similarity

    results: list[EmbeddingResult] = []
    query_len = len(query)

    for cand in candidates:
        if exclude_track_id is not None and cand.track.id == exclude_track_id:
            continue
        if len(cand.vector) != query_len:
            continue
        try:
            score = cosine_similarity(query, cand.vector)
        except (ValueError, TypeError) as exc:
            # Ungültige Werte oder Typen überspringen (kein Abbruch der Suche)
            log.debug("Überspringe Kandidat %s bei Kosinus-Ähnlichkeit: %s", cand.track.id, exc)
            continue
        if score < min_score:
            continue
        results.append(EmbeddingResult(cand.track, score))

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:top_n]


def format_embedding_list(results: list[EmbeddingResult]) -> str:
    """Formatiert Treffer der klanglichen Ähnlichkeitssuche als Bot-Text.

    Erste Zeile ist '🎧 Klanglich ähnlich:', danach je Ergebnis eine Zeile:
    '{rank}. {artist} – {title} · {bpm} BPM · {key} · Ähnlichkeit {score * 100:.1f}%'
    (Rang ab 1; bpm/key als '–' wenn None/fehlend, ebenso artist/title).
    Leere Liste -> genau `NO_SIMILAR`.
    """
    if not results:
        return NO_SIMILAR

    lines = ["🎧 Klanglich ähnlich:"]
    for rank, result in enumerate(results, start=1):
        rec = result.track
        artist = rec.artist or "–"
        title = rec.title or "–"
        bpm = "–" if rec.bpm is None else f"{rec.bpm}"
        key = rec.key_camelot or "–"
        lines.append(
            f"{rank}. {artist} – {title} · {bpm} BPM · {key} · Ähnlichkeit {result.score * 100:.1f}%"
        )
    return "\n".join(lines)


def build_similar_by_track(db: "TrackDB", track_id: int, *, top_n: int = 5) -> str | None:
    """Sucht klanglich ähnliche Aufnahmen anhand des Audio-Embeddings eines Tracks.

    Gibt None zurück, wenn für den Track kein Embedding vorliegt (Signal für den
    Aufrufer, auf Chromaprint zurückzufallen).
    Fehler (z. B. DB nicht lesbar) werden geloggt; Rückgabe dann `SIMILAR_FAILED`.
    Wirft nie.
    """
    try:
        emb = db.get_embedding(track_id)
        if emb is None:
            return None
        model, query_vec = emb

        candidates: list[EmbeddingCandidate] = []
        for track, vec in db.embedded_tracks():
            if track.id == track_id:
                continue
            cand_emb = db.get_embedding(track.id)
            if cand_emb is not None and cand_emb[0] == model:
                candidates.append(EmbeddingCandidate(track, vec))

        results = find_similar_embeddings(
            query_vec, candidates, top_n=top_n, exclude_track_id=track_id
        )
        return format_embedding_list(results)
    except Exception:
        log.exception("Embedding-basierte Ähnlichkeitssuche fehlgeschlagen")
        return SIMILAR_FAILED
