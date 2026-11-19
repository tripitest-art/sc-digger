#!/usr/bin/env python3
"""Set-Tracklist aus MixesDB lesen und mit der eigenen Sammlung abgleichen.

MixesDB dokumentiert DJ-Sets aus der Schranz- und Hard-Techno-Szene samt Promos, White
Labels und Edits, die keine Audio-Erkennung findet. Das Skript sucht die Seite zu einem
Set, liest die Tracklist und markiert jeden Eintrag mit ✓ (in der Sammlung), ✗ (fehlt)
oder ? (im Wiki nicht benannt).

Verwendung:
    # Seiten zu einem Set suchen:
    python scripts/mixesdb_set.py --suche "Robert Natus Schranz Total"

    # Tracklist einer Seite zeigen, mit Abgleich gegen die Track-DB:
    python scripts/mixesdb_set.py --seite "2006-03-03 - Robert Natus @ Schranz Total, PPC, Graz"

    # Nur die fehlenden Tracks als kopierbare Liste:
    python scripts/mixesdb_set.py --seite "..." --nur-fehlen

    # Ohne Datenbank (nur Wiki-Inhalt, alles gilt als fehlend):
    python scripts/mixesdb_set.py --seite "..." --ohne-db
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sc_digger import mixesdb
from sc_digger.collection import name_key
from sc_digger.models import Config
from sc_digger.db import TrackDB


def _collection_names(db_path: Path | str) -> list[tuple[str | None, str]]:
    """Artist/Titel aller Tracks der Sammlung (Status archive und adopted)."""
    with TrackDB(db_path) as db:
        records = db.list_collection_tracks()
    names: list[tuple[str | None, str]] = []
    for record in records:
        title = getattr(record, "title", None) or None
        artist = getattr(record, "artist", None) or None
        if title:
            names.append((artist, title))
    return names


def _page_from_hit(hit: str) -> str:
    """Seitentitel oder Wiki-URL zu einem Seitentitel normalisieren."""
    if hit.startswith("http"):
        tail = hit.split("/w/", 1)[-1]
        return tail.replace("_", " ").split("#", 1)[0].strip()
    return hit.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--suche", help="Freie Suche in MixesDB (DJ, Event, Jahr)")
    parser.add_argument("--limit", type=int, default=10, help="Treffer der Suche (Standard 10)")
    parser.add_argument("--seite", action="append", default=[],
                        help="Seitentitel oder Wiki-URL einer Tracklist (mehrfach erlaubt)")
    parser.add_argument("--db", help="Pfad zur Track-DB (Standard: config.yaml)")
    parser.add_argument("--ohne-db", action="store_true", help="Ohne Sammlungsabgleich")
    parser.add_argument("--nur-fehlen", action="store_true",
                        help="Nur fehlende Tracks, eine Zeile je Track")
    parser.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if not (args.suche or args.seite):
        parser.error("--suche oder --seite angeben")

    if args.suche:
        hits = mixesdb.search_sets(args.suche, limit=args.limit)
        if args.json:
            print(json.dumps({"suche": args.suche, "treffer": hits}, ensure_ascii=False, indent=2))
        else:
            if not hits:
                print(f"Keine MixesDB-Seite zu {args.suche!r} gefunden.")
            for hit in hits:
                print(f"{mixesdb.setlist_url(hit)}")
        if not args.seite:
            return 0

    names: list[tuple[str | None, str]] | None = None
    if not args.ohne_db:
        if args.db:
            db_path: Path | str = args.db
        else:
            cfg = Config.load("config.yaml")
            db_path = Path(cfg["state"]["track_db_path"])
        if not Path(db_path).exists():
            print(f"Track-DB {db_path} fehlt — mit --ohne-db arbeiten.", file=sys.stderr)
            return 2
        names = _collection_names(db_path)

    reports = []
    for page in (_page_from_hit(p) for p in args.seite):
        setlist = mixesdb.fetch_setlist(page)
        if setlist is None:
            print(f"Seite {page!r} nicht gefunden.", file=sys.stderr)
            continue
        check = mixesdb.check_setlist(setlist, names or [])
        reports.append(check)
        if args.json:
            continue
        if args.nur_fehlen:
            for line in mixesdb.missing_tracks(check):
                print(line)
        else:
            print(mixesdb.format_setlist(check))
            if args.seite and len(args.seite) > 1:
                print()
        print(f"Quelle: {setlist.url}\n", file=sys.stderr if args.nur_fehlen else sys.stdout)

    if args.json:
        payload = [
            {
                "seite": check.setlist.page,
                "url": check.setlist.url,
                "dauer_s": check.setlist.duration_s,
                "eintraege": [
                    {
                        "position_s": entry.position_s,
                        "artist": entry.artist,
                        "titel": entry.title,
                        "label": entry.label,
                        "katalog": entry.catalog,
                        "offen": entry.unknown,
                        "vorhanden": owned,
                    }
                    for entry, owned in zip(check.setlist.entries, check.owned)
                ],
            }
            for check in reports
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
