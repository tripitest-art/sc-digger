"""Zuordnungslogik: Sammlungsdateien mit SoundCloud-Tracks verknüpfen (Teil 1).

Reine Logik ohne Netzwerk: aus vorhandenen Datenbankzeilen (``TrackRecord``) und bereits
geladenen SoundCloud-Tracks (``Track``) entsteht ein Vorschlag, der als ``source_url`` in
``tracks.sqlite`` geschrieben wird. Die Namen vergleicht ``collection.name_key`` /
``names_match`` wieder, damit dieselben Remixer-Regeln gelten wie beim Duplikat-Check.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit, urlunsplit

from .collection import name_key, names_match
from .db import TrackDB, TrackRecord
from .models import Track

log = logging.getLogger(__name__)

# Sammlungsdateien tragen die SoundCloud-ID oft im Namen ("[12345] Artist - Titel.wav").
# Die ID sagt nichts über den Track aus und gehört nicht in den Vergleichsnamen.
_LEADING_ID = re.compile(r"^\[\d+\]\s*")


def normalize_url(url: str | None) -> str:
    """Vergleichsform einer SoundCloud-URL.

    Rand-Leerzeichen weg, Schema immer ``https``, Host klein und ohne ``www.``/``m.``,
    Pfad klein und ohne abschließendes ``/``, Query und Fragment entfernt. ``None`` oder
    leer ergibt ``""``. Wirft nie: was sich nicht zerlegen lässt, gilt als leer.
    """
    if url is None or not url.strip():
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    elif host.startswith("m."):
        host = host[2:]
    return urlunsplit(("https", host, parts.path.lower().rstrip("/"), "", ""))


@dataclass(frozen=True)
class LinkProposal:
    """Vorschlag, die Zeile ``record_id`` mit dem SoundCloud-Track ``sc_id`` zu verknüpfen."""

    record_id: int      # tracks.id
    path: str           # tracks.path
    sc_id: int          # Track.id
    url: str            # Track.url (Permalink, unverändert)
    title: str          # Track.title
    ambiguous: bool     # True, wenn mehrere SoundCloud-Tracks passen


def _record_key(record: TrackRecord):
    """Vergleichs-Schlüssel einer Datenbankzeile.

    Sind Künstler und Titel getaggt, zählen die Felder. Sonst bleibt nur der Dateiname:
    die Sammlungsdateien sind überwiegend untagged (nur 3 von 470 Zeilen tragen eine URL),
    deshalb ist der Dateiname der Normalfall, nicht der Notfall.
    """
    if (record.title or "").strip():
        return name_key(record.artist, record.title)
    stem = _LEADING_ID.sub("", Path(record.path).stem)
    return name_key(None, stem)


def propose_links(
    records: Sequence[TrackRecord],
    candidates: Sequence[Track],
) -> list[LinkProposal]:
    """Genau ein Vorschlag je Datenbankzeile, die passt.

    Übersprungen werden Zeilen ohne ``id`` und Zeilen, deren ``source_url`` schon gesetzt
    ist – eine bestehende Verknüpfung wird hier nicht angezweifelt. Ein Kandidat passt,
    wenn sein Künstler-/Titel-Schlüssel oder sein reiner Titel-Schlüssel passt; SoundCloud-
    Titel enthalten oft schon „Artist - Titel“. Passen mehrere, gewinnt der Kandidat mit den
    meisten gemeinsamen Wörtern, bei Gleichstand der erste in ``candidates``; der Vorschlag
    gilt dann als unsicher. Reihenfolge der Rückgabe wie ``records``.
    """
    proposals: list[LinkProposal] = []
    for record in records:
        if record.id is None or (record.source_url or "").strip():
            continue
        key = _record_key(record)
        matched: list[tuple[int, Track]] = []
        for cand in candidates:
            key_artist = name_key(cand.artist, cand.title)
            key_title = name_key(None, cand.title)
            if not (names_match(key, key_artist) or names_match(key, key_title)):
                continue
            overlap = max(len(key.tokens & key_artist.tokens), len(key.tokens & key_title.tokens))
            matched.append((overlap, cand))
        if not matched:
            continue
        # max() liefert bei gleichem Zähler den ersten Treffer -> "erster in candidates".
        best = max(matched, key=lambda m: m[0])[1]
        if len(matched) > 1:
            log.info(
                "Mehrdeutig: %s passt auf %d SoundCloud-Tracks, nehme #%s",
                record.path, len(matched), best.id,
            )
        proposals.append(LinkProposal(
            record_id=record.id,
            path=record.path,
            sc_id=best.id,
            url=best.url,
            title=best.title,
            ambiguous=len(matched) > 1,
        ))
    return proposals


def apply_proposals(
    db: TrackDB,
    proposals: Sequence[LinkProposal],
    *,
    include_ambiguous: bool = False,
) -> int:
    """Schreibt die Vorschläge als ``source_url`` in die Datenbank und gibt die Anzahl zurück.

    Geschrieben wird nur, wo ``source_url`` NULL oder leer ist: die Verknüpfung ist eine
    Ergänzung, kein Ersatz. Unsichere Vorschläge bleiben ohne ``include_ambiguous=True``
    außen vor, damit ein falscher Treffer nicht in der Sammlung landet.
    """
    changed = 0
    for proposal in proposals:
        if proposal.ambiguous and not include_ambiguous:
            continue
        cur = db.db.execute(
            "UPDATE tracks SET source_url = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND (source_url IS NULL OR source_url = '')",
            (proposal.url, proposal.record_id),
        )
        changed += cur.rowcount
    db.db.commit()
    return changed
