"""Kuratoren-Übersicht aus der Sammlung (Issue #567).

Gruppiert Sammlungs-Tracks (status archive/adopted) nach Artist,
berechnet Anzahl, Anteil gut bewerteter Tracks (rb_rating >= 4),
mittleres Tempo und sortiert Tracks nach BPM aufsteigend.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .db import TrackRecord

if TYPE_CHECKING:
    from .db import TrackDB

# Höchstzahl der Mining-Kandidaten in Bot-Text, Datei und Dashboard.
MAX_CANDIDATES = 30


def candidates_from_counts(counts: Mapping[str, int], *, min_appearances: int = 2,
                           limit: int = MAX_CANDIDATES) -> list[tuple[str, int]]:
    """[(profil, anzahl)] ab min_appearances Treffern, absteigend nach Anzahl, bei Gleichstand
    alphabetisch nach Profil; höchstens `limit` Einträge. [] bei leerem Mapping."""
    picked = [(profile, count) for profile, count in counts.items() if count >= min_appearances]
    picked.sort(key=lambda item: (-item[1], item[0]))
    return picked[:max(0, limit)]


def read_candidates(candidates_path: str | Path) -> dict[str, Any]:
    """{"candidates": [{"profile": str, "appearances": int}], "checked_at": str|None}.
    Fehlende oder unlesbare Datei -> {"candidates": [], "checked_at": None}."""
    empty: dict[str, Any] = {"candidates": [], "checked_at": None}
    try:
        data = json.loads(Path(candidates_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
        return empty
    candidates = [
        {"profile": str(item["profile"]), "appearances": int(item["appearances"])}
        for item in data["candidates"]
        if isinstance(item, dict) and isinstance(item.get("profile"), str)
        and isinstance(item.get("appearances"), int) and not isinstance(item.get("appearances"), bool)
    ]
    checked_at = data.get("checked_at")
    return {"candidates": candidates,
            "checked_at": checked_at if isinstance(checked_at, str) else None}


def write_candidates(candidates_path: str | Path, candidates: list[tuple[str, int]]) -> None:
    """Schreibt die letzte Auswertung atomar (gleiche Form, die `read_candidates` liest)."""
    path = Path(candidates_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "candidates": [{"profile": p, "appearances": n} for p, n in candidates],
        "checked_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def candidates_file(cfg: Any) -> Path:
    """Ablage der letzten Auswertung: neben der State-DB."""
    return Path(cfg["state"]["db_path"]).parent / "curator-candidates.json"


@dataclass(frozen=True)
class CuratorEntry:
    artist: str
    track_count: int
    rated_share: float          # Anteil der Tracks mit rb_rating >= 4, 0.0..1.0
    avg_bpm: float | None       # Mittel über die Tracks mit gesetztem bpm, sonst None
    tracks: list[TrackRecord]   # nach bpm aufsteigend, Tracks ohne bpm zuletzt


def curator_overview(
    db: TrackDB, *, min_tracks: int = 3, limit: int = 30
) -> list[CuratorEntry]:
    """Artists der Sammlung (status 'archive' oder 'adopted', artist nicht leer).

    Gruppiert nach `artist` (exakt, ohne Normalisierung), nur Gruppen mit mindestens
    `min_tracks` Tracks. Sortierung: track_count absteigend, bei Gleichstand artist
    alphabetisch. Danach die ersten `limit` Einträge.
    """
    if hasattr(db, "list_collection_tracks"):
        raw_tracks = db.list_collection_tracks()
    else:
        conn = getattr(db, "db", db)
        rows = conn.execute(
            "SELECT * FROM tracks WHERE status IN ('archive', 'adopted') ORDER BY id ASC"
        ).fetchall()
        raw_tracks = [TrackRecord.from_row(r) for r in rows]

    grouped: dict[str, list[TrackRecord]] = {}
    for track in raw_tracks:
        if not track.artist or not track.artist.strip():
            continue
        grouped.setdefault(track.artist, []).append(track)

    entries: list[CuratorEntry] = []
    for artist, tracks in grouped.items():
        if len(tracks) < min_tracks:
            continue

        # Tracks nach bpm aufsteigend sortieren, Tracks ohne bpm zuletzt
        sorted_tracks = sorted(
            tracks,
            key=lambda t: (t.bpm is None, t.bpm if t.bpm is not None else 0.0),
        )

        # Anteil rb_rating >= 4
        rated_count = sum(
            1 for t in tracks if t.rb_rating is not None and t.rb_rating >= 4
        )
        rated_share = rated_count / len(tracks) if tracks else 0.0

        # Mittelwert über Tracks mit gesetztem bpm
        bpm_values = [t.bpm for t in tracks if t.bpm is not None]
        avg_bpm = sum(bpm_values) / len(bpm_values) if bpm_values else None

        entries.append(
            CuratorEntry(
                artist=artist,
                track_count=len(tracks),
                rated_share=rated_share,
                avg_bpm=avg_bpm,
                tracks=sorted_tracks,
            )
        )

    # Sortierung: track_count absteigend, bei Gleichstand artist alphabetisch
    entries.sort(key=lambda e: (-e.track_count, e.artist))

    if limit is not None:
        if limit <= 0:
            return []
        entries = entries[:limit]

    return entries
