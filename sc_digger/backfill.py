"""Energie-Backfill (Issue #537): Stufe 1..5 für bereits erfasste Tracks nachtragen.

Schreibt ausschließlich in die Track-DB. Die Sammlung ist auf dem Server read-only
eingebunden (und Regel 4 verbietet Änderungen), deshalb werden hier bewusst keine
Datei-Tags geschrieben.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from .analysis import compute_energy_features
from .db import TrackDB
from .style import calculate_energy

log = logging.getLogger(__name__)

PROGRESS_EVERY = 50


def backfill_energy(
    db: TrackDB,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """Berechnet die Energie-Stufe für Tracks mit `energy IS NULL`.

    Rückgabe: seen (betrachtet), set (geschrieben), skipped (hatten schon eine Stufe),
    failed (Datei fehlt/nicht lesbar), would_set (im Probelauf berechnet, nicht
    geschrieben). `progress(erledigt, gesamt)` wird alle 50 Tracks aufgerufen.
    """
    skipped = db.count_tracks_with_energy()
    offen = db.tracks_without_energy(limit)
    res = {"seen": skipped + len(offen), "set": 0, "skipped": skipped, "failed": 0, "would_set": 0}
    gesamt = len(offen)
    for i, rec in enumerate(offen, 1):
        try:
            feats = compute_energy_features(Path(rec.path))
            centroid = feats.get("spectral_centroid_hz")
            if centroid is None:
                res["failed"] += 1
                log.warning("Energie-Backfill: keine Features für %s", Path(rec.path).name)
            else:
                stufe = int(calculate_energy(
                    bpm=rec.bpm, spectral_centroid_hz=centroid,
                    rms=feats.get("rms"), subgenre=None,
                ))
                if dry_run:
                    res["would_set"] += 1
                elif db.set_energy(rec.id, stufe):
                    res["set"] += 1
                else:
                    res["failed"] += 1
        except Exception as e:  # ein defekter Track bricht den Lauf nicht ab
            res["failed"] += 1
            log.warning("Energie-Backfill: %s übersprungen: %s", Path(rec.path).name, e)
        if progress and (i % PROGRESS_EVERY == 0):
            progress(i, gesamt)
    return res
