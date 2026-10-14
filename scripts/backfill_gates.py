#!/usr/bin/env python3
"""Wartungsskript: Backfill von Gated Tracks aus seen.sqlite in tracks.sqlite.

Liest alle jemals im Digest gemeldeten Tracks aus seen.sqlite, prüft, welche
noch nicht in tracks.sqlite (weder als Gate noch als Store) vorhanden sind,
lädt deren Metadaten per SoundCloud api-v2 in Batches nach und trägt erkannte
Gates (Hypeddit, Droploud, Toneden, The Artist Union) in die Tabelle gate_items ein.

Verwendung:
    # Auf dem Server im Docker-Container:
    docker compose exec sc-digger python scripts/backfill_gates.py

    # Trockenlauf (ohne Schreiben in DB):
    docker compose exec sc-digger python scripts/backfill_gates.py --dry-run

    # Mit expliziten Pfaden:
    python scripts/backfill_gates.py --seen /path/to/seen.sqlite --track-db /path/to/tracks.sqlite
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

# Projekt-Root zum sys.path hinzufügen, falls das Skript direkt ausgeführt wird
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sc_digger.db import TrackDB
from sc_digger.models import Config, DownloadKind, Track
from sc_digger.output import _GATE_KINDS
from sc_digger.pipeline import classify_download
from sc_digger.soundcloud import SoundCloudClient

log = logging.getLogger("backfill_gates")


@dataclass
class BackfillStats:
    total_seen: int = 0
    already_persisted: int = 0
    checked: int = 0
    gates_found: int = 0
    stores_found: int = 0
    stream_only: int = 0
    errors: int = 0


def fetch_tracks_batch(
    sc: SoundCloudClient,
    track_ids: Sequence[int],
    batch_size: int = 30,
) -> list[Track]:
    """Lädt Metadaten zu mehreren SoundCloud-Track-IDs in Chunks via /tracks?ids=."""
    tracks: list[Track] = []
    for i in range(0, len(track_ids), batch_size):
        chunk = track_ids[i : i + batch_size]
        try:
            data = sc._get("/tracks", {"ids": ",".join(map(str, chunk))})
            items = data if isinstance(data, list) else data.get("collection", [])
            for item in items:
                t = sc._to_track(item)
                if t:
                    tracks.append(t)
        except Exception as e:
            log.warning("Fehler beim Batch-Abruf der IDs %s: %s", chunk, e)
    return tracks


def run_backfill(
    seen_db_path: Path | str,
    track_db_path: Path | str,
    sc_client: SoundCloudClient | None = None,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    batch_size: int = 30,
) -> BackfillStats:
    seen_path = Path(seen_db_path)
    track_path = Path(track_db_path)

    if not seen_path.is_file():
        raise FileNotFoundError(f"seen.sqlite nicht gefunden unter: {seen_path}")

    stats = BackfillStats()

    # 1. Alle Einträge aus seen.sqlite lesen
    with sqlite3.connect(seen_path) as sdb:
        sdb.row_factory = sqlite3.Row
        cur = sdb.execute("SELECT id, url, title, reported_at FROM seen ORDER BY id DESC")
        seen_rows = cur.fetchall()

    stats.total_seen = len(seen_rows)
    if not seen_rows:
        log.info("seen.sqlite enthält keine Einträge.")
        return stats

    # 2. Bereits vorhandene Items aus TrackDB abfragen
    with TrackDB(track_path) as db:
        existing_gates = {g["sc_id"] for g in db.get_gate_items()}
        existing_stores = {s["sc_id"] for s in db.get_store_items()}

    # 3. Kandidaten filtern
    candidates = [
        r for r in seen_rows
        if r["id"] not in existing_gates and r["id"] not in existing_stores
    ]
    stats.already_persisted = stats.total_seen - len(candidates)

    if limit is not None and limit > 0:
        candidates = candidates[:limit]

    if not candidates:
        log.info("Keine neuen Kandidaten zum Prüfen vorhanden (alle bereits in gate_items/store_items).")
        return stats

    log.info(
        "Starte Backfill: %d von %d Tracks zu prüfen (Bereits erfasst: %d)...",
        len(candidates),
        stats.total_seen,
        stats.already_persisted,
    )

    # 4. SoundCloud Client vorbereiten
    sc = sc_client or SoundCloudClient()

    candidate_ids = [r["id"] for r in candidates]
    fetched_tracks = fetch_tracks_batch(sc, candidate_ids, batch_size=batch_size)
    fetched_by_id = {t.id: t for t in fetched_tracks}

    stats.checked = len(candidates)
    stats.errors = len(candidate_ids) - len(fetched_by_id)

    # 5. Klassifizieren & Speichern
    with TrackDB(track_path) as db:
        for r in candidates:
            sc_id = r["id"]
            track = fetched_by_id.get(sc_id)
            if not track:
                continue

            classify_download(track)

            if track.download_kind in _GATE_KINDS and track.download_link:
                stats.gates_found += 1
                log.info(
                    "🚪 Gate gefunden: %s - %s (%s: %s)",
                    track.artist,
                    track.title,
                    track.download_kind.value,
                    track.download_link,
                )
                if not dry_run:
                    db.upsert_gate_item(
                        sc_id=track.id,
                        title=track.title,
                        artist=track.artist,
                        gate_url=track.download_link,
                        gate_type=track.download_kind.value,
                    )
            elif track.download_kind == DownloadKind.STORE and track.purchase_url:
                stats.stores_found += 1
                log.info(
                    "🛒 Store gefunden: %s - %s (%s)",
                    track.artist,
                    track.title,
                    track.purchase_url,
                )
                if not dry_run:
                    db.upsert_store_item(
                        sc_id=track.id,
                        title=track.title,
                        artist=track.artist,
                        purchase_url=track.purchase_url,
                        purchase_title=track.purchase_title,
                    )
            else:
                stats.stream_only += 1

    return stats


def resolve_paths(
    config_path: Path,
    seen_arg: str | None,
    track_arg: str | None,
) -> tuple[Path, Path]:
    """Ermittelt die Pfade zu seen.sqlite und tracks.sqlite."""
    seen_path: Path | None = Path(seen_arg) if seen_arg else None
    track_path: Path | None = Path(track_arg) if track_arg else None

    if (not seen_path or not track_path) and config_path.is_file():
        try:
            cfg = Config.load(config_path)
            state_cfg = cfg.raw.get("state") or {}
            if not seen_path and "db_path" in state_cfg:
                seen_path = Path(state_cfg["db_path"])
            if not track_path and "track_db_path" in state_cfg:
                track_path = Path(state_cfg["track_db_path"])
        except Exception as e:
            log.warning("Konnte %s nicht laden: %s", config_path, e)

    # Standard-Fallbacks
    if not seen_path:
        # Prüfe lokale / Container-Pfade
        for fallback in [Path("/data/seen.sqlite"), Path("data/seen.sqlite"), Path("seen.sqlite")]:
            if fallback.exists():
                seen_path = fallback
                break
        if not seen_path:
            seen_path = Path("/data/seen.sqlite")

    if not track_path:
        for fallback in [Path("/data/tracks.sqlite"), Path("data/tracks.sqlite"), Path("tracks.sqlite")]:
            if fallback.exists():
                track_path = fallback
                break
        if not track_path:
            track_path = Path("/data/tracks.sqlite")

    return seen_path, track_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill von Gate-Items aus seen.sqlite in tracks.sqlite."
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Pfad zur config.yaml (Standard: config.yaml)",
    )
    parser.add_argument(
        "--seen",
        default=None,
        help="Expliziter Pfad zu seen.sqlite (überschreibt config.yaml)",
    )
    parser.add_argument(
        "--track-db",
        default=None,
        help="Expliziter Pfad zu tracks.sqlite (überschreibt config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur analysieren und anzeigen, keine Einträge in DB schreiben",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximal N Kandidaten prüfen",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=30,
        help="Batch-Größe für SoundCloud-Abfragen (Standard: 30)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Ausführliche Logging-Ausgabe",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    seen_path, track_path = resolve_paths(Path(args.config), args.seen, args.track_db)

    print(f"=== sc-digger Gate-Backfill ===")
    print(f"seen.sqlite:   {seen_path}")
    print(f"tracks.sqlite: {track_path}")
    print(f"Dry-Run:       {args.dry_run}")
    print("--------------------------------")

    try:
        stats = run_backfill(
            seen_db_path=seen_path,
            track_db_path=track_path,
            dry_run=args.dry_run,
            limit=args.limit,
            batch_size=args.batch_size,
        )
    except FileNotFoundError as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        log.exception("Fehler während des Backfills: %s", e)
        return 1

    print("\n=== Zusammenfassung ===")
    print(f"Gesamt in seen.sqlite:       {stats.total_seen}")
    print(f"Bereits erfasst (überspr.):   {stats.already_persisted}")
    print(f"Geprüft via SoundCloud:      {stats.checked}")
    print(f"  -> Gate-Items gefunden:    {stats.gates_found}" + (" (nur simuliert)" if args.dry_run else " (gespeichert)"))
    print(f"  -> Store-Items gefunden:   {stats.stores_found}" + (" (nur simuliert)" if args.dry_run else " (gespeichert)"))
    print(f"  -> Nur Stream / Kein Gate: {stats.stream_only}")
    print(f"  -> Nicht abrufbar/Fehler:  {stats.errors}")
    print("========================")

    return 0


if __name__ == "__main__":
    sys.exit(main())
