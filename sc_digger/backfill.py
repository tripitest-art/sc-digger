"""Energie-Backfill (Issue #537): Stufe 1..5 für bereits erfasste Tracks nachtragen.

Schreibt ausschließlich in die Track-DB. Die Sammlung ist auf dem Server read-only
eingebunden (und Regel 4 verbietet Änderungen), deshalb werden hier bewusst keine
Datei-Tags geschrieben.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from .analysis import analyze_track, compute_energy_features
from .db import TrackDB
from .style import calculate_energy

log = logging.getLogger(__name__)

PROGRESS_EVERY = 50


def backfill_energy(
    db: TrackDB,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    force: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """Berechnet die Energie-Stufe für Tracks.

    force=True: alle Tracks neu messen (auch die mit vorhandener Energie),
    damit fehlende Einzelwerte wie spectral_centroid_hz nachgezogen werden.
    Ohne force: nur Tracks mit `energy IS NULL`.

    Rückgabe: seen (betrachtet), set (geschrieben), skipped (hatten schon eine Stufe),
    failed (Datei fehlt/nicht lesbar), would_set (im Probelauf berechnet, nicht
    geschrieben). `progress(erledigt, gesamt)` wird alle 50 Tracks aufgerufen.
    """
    if force:
        skipped = 0
        offen = db.list_tracks(limit=limit)
    else:
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
                    db.set_spectral_centroid(rec.id, centroid)
                    res["set"] += 1
                else:
                    res["failed"] += 1
        except Exception as e:  # ein defekter Track bricht den Lauf nicht ab
            res["failed"] += 1
            log.warning("Energie-Backfill: %s übersprungen: %s", Path(rec.path).name, e)
        if progress and (i % PROGRESS_EVERY == 0):
            progress(i, gesamt)
    return res


def backfill_bpm(
    db: TrackDB,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    snap_to: tuple[float, float] | None = (140.0, 170.0),
) -> dict[str, int]:
    """Misst Altwerte der BPM neu (Issue #703).

    Altwerte (`bpm_source = 'audio'`, keine Konfidenz) stammen aus der Raster-Erkennung vor
    #287 (161,5/152,0). Fehlende Datei, Analysefehler oder kein BPM zählen als `failed`; die
    Zeile bleibt dann unverändert. Auch bestätigte Werte bekommen die Konfidenz, damit sie nicht
    erneut als Altwert gelten. Liest Audio nur, schreibt nur in die Track-DB (Regel 4).
    Rückgabe: seen, set, would_set, unchanged, failed."""
    offen = db.tracks_with_legacy_bpm(limit)
    res = {"seen": len(offen), "set": 0, "would_set": 0, "unchanged": 0, "failed": 0}
    for rec in offen:
        p = Path(rec.path)
        try:
            if not p.is_file():
                raise FileNotFoundError(p.name)
            analyse = analyze_track(p, want_key=False, snap_to=snap_to)
            neu = analyse.get("bpm")
            if not neu:
                raise ValueError("kein BPM erkannt")
            neu = float(neu)
            konfidenz = float(analyse.get("bpm_confidence") or 0.0)
        except Exception as e:  # ein defekter Track bricht den Lauf nicht ab
            res["failed"] += 1
            log.warning("BPM-Backfill: %s übersprungen: %s", p.name, e)
            continue
        if dry_run:
            res["would_set"] += 1
            continue
        db.set_bpm(rec.id, neu, konfidenz)
        if abs(neu - float(rec.bpm)) < 0.05:
            res["unchanged"] += 1
        else:
            res["set"] += 1
    return res


def backfill_names(db: TrackDB, *, dry_run: bool = False, limit: int | None = None) -> dict[str, int]:
    """Ergänzt fehlende Artists (und Titel) aus Tags oder Dateinamen (Issue #702).

    Schreibt nur in die Track-DB, nie in Dateien (Goldene Regel 4). Ohne ermittelbaren
    Artist wird übersprungen. Rückgabe: seen, set, would_set, skipped."""
    from .audit import names_for_file

    offen = db.tracks_without_artist(limit)
    res = {"seen": len(offen), "set": 0, "would_set": 0, "skipped": 0}
    for rec in offen:
        artist, title = names_for_file(Path(rec.path))
        if not artist:
            res["skipped"] += 1
            continue
        if dry_run:
            res["would_set"] += 1
        elif db.set_names(rec.id, artist, title):
            res["set"] += 1
    return res
