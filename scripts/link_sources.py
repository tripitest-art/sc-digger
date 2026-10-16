#!/usr/bin/env python3
"""Wartungsskript: Sammlung (Status archive) mit SoundCloud verknüpfen (Teil 2).

Liest alle Zeilen mit status="archive" und leerer source_url aus tracks.sqlite,
sammelt Kandidaten aus angegebenen SoundCloud-Likes und -Playlists und gleicht
sie über linksource.propose_links ab. Optional wird für Zeilen ohne Treffer
die freie SoundCloud-Suche bemüht.
Standardmäßig Trockenlauf mit CSV-Vorschau; erst mit --apply wird die Datenbank
aktualisiert.

Verwendung:
    # Vorschau (Dry-Run) mit Likes und Playlists, Ausgabe in CSV-Report:
    python scripts/link_sources.py --likes https://soundcloud.com/user/likes --playlist https://soundcloud.com/user/sets/liste --report vorschlag.csv

    # Mit zusätzlicher Suche für unerkannte Tracks:
    python scripts/link_sources.py --likes https://soundcloud.com/user/likes --search --search-limit 5 --report vorschlag.csv

    # Eindeutige Treffer in tracks.sqlite schreiben:
    python scripts/link_sources.py --likes https://soundcloud.com/user/likes --apply

    # Auch mehrdeutige Treffer übernehmen:
    python scripts/link_sources.py --likes https://soundcloud.com/user/likes --apply --include-ambiguous
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
from dataclasses import dataclass
import logging
from pathlib import Path
import re
import sys
from typing import Sequence

# Projekt-Root zum sys.path hinzufügen, falls das Skript direkt ausgeführt wird
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sc_digger import linksource
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.linksource import LinkProposal
from sc_digger.models import Config, Track
from sc_digger.soundcloud import SoundCloudClient, SoundCloudError

log = logging.getLogger("link_sources")

_LEADING_ID = re.compile(r"^\[\d+\]\s*")


@dataclass
class LinkStats:
    records: int = 0      # Zeilen mit status "archive" und leerer source_url
    candidates: int = 0   # SoundCloud-Tracks aus Likes und Playlists
    proposals: int = 0    # Vorschläge insgesamt (inkl. Suche)
    ambiguous: int = 0    # davon unsicher
    applied: int = 0      # geschriebene Zeilen


def _search_query(record: TrackRecord) -> str:
    """Suchanfrage für die freie SoundCloud-Suche."""
    if (record.artist or "").strip() and (record.title or "").strip():
        return f"{record.artist} - {record.title}"
    stem = _LEADING_ID.sub("", Path(record.path).stem)
    return stem


def run_link(
    sc,
    db: TrackDB,
    *,
    likes: Sequence[str] = (),
    playlists: Sequence[str] = (),
    search: bool = False,
    search_limit: int = 5,
    apply: bool = False,
    include_ambiguous: bool = False,
    report: Path | None = None,
) -> LinkStats:
    """1. records = db.list_tracks(status="archive") ohne source_url.
    2. Kandidaten: sc.likes_of_user(url, limit=2000)[1] je Eintrag in likes, sc.playlist_tracks(url)[1]
       je Eintrag in playlists. Wirft eine Quelle SoundCloudError: log.warning, weiter mit der nächsten.
    3. proposals = linksource.propose_links(records, Kandidaten); Quelle "likes/playlist".
    4. search=True: für jede Zeile OHNE Vorschlag sc.search_tracks(Anfrage, limit=search_limit), Anfrage =
       f"{artist} - {title}", wenn beide gesetzt, sonst der Dateiname ohne Endung und ohne führendes
       "[<id>] ". Treffer über propose_links; diese Vorschläge gelten IMMER als unsicher
       (ambiguous=True, dataclasses.replace), Quelle "suche".
    5. report: UTF-8-CSV, Trennzeichen ";", Kopf record_id;path;url;title;ambiguous;quelle, eine Zeile je Vorschlag.
    6. apply=True: linksource.apply_proposals(db, proposals, include_ambiguous=include_ambiguous).
       Ohne apply wird die Datenbank nicht verändert.
    Es werden nur Methoden von sc aufgerufen: likes_of_user, playlist_tracks, search_tracks."""
    records = [r for r in db.list_tracks(status="archive") if not (r.source_url or "").strip()]

    stats = LinkStats(records=len(records))

    candidates: list[Track] = []
    for url in likes:
        try:
            _, tracks = sc.likes_of_user(url, limit=2000)
            candidates.extend(tracks)
        except SoundCloudError as e:
            log.warning("Fehler beim Laden der Likes von %s: %s", url, e)

    for url in playlists:
        try:
            _, tracks = sc.playlist_tracks(url)
            candidates.extend(tracks)
        except SoundCloudError as e:
            log.warning("Fehler beim Laden der Playlist %s: %s", url, e)

    stats.candidates = len(candidates)

    base_proposals = linksource.propose_links(records, candidates)
    all_proposals: list[LinkProposal] = []
    sources: list[str] = []

    for prop in base_proposals:
        all_proposals.append(prop)
        sources.append("likes/playlist")

    matched_record_ids = {p.record_id for p in base_proposals}

    if search:
        for record in records:
            if record.id is not None and record.id in matched_record_ids:
                continue
            query = _search_query(record)
            try:
                hits = sc.search_tracks(query, limit=search_limit)
            except SoundCloudError as e:
                log.warning("Suche fehlgeschlagen für %r: %s", query, e)
                continue
            search_props = linksource.propose_links([record], hits)
            if search_props:
                prop = dataclasses.replace(search_props[0], ambiguous=True)
                all_proposals.append(prop)
                sources.append("suche")

    stats.proposals = len(all_proposals)
    stats.ambiguous = sum(1 for p in all_proposals if p.ambiguous)

    if report is not None:
        report_path = Path(report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["record_id", "path", "url", "title", "ambiguous", "quelle"])
            for prop, quelle in zip(all_proposals, sources):
                writer.writerow([
                    str(prop.record_id),
                    prop.path,
                    prop.url,
                    prop.title,
                    str(prop.ambiguous),
                    quelle,
                ])

    if apply:
        stats.applied = linksource.apply_proposals(
            db, all_proposals, include_ambiguous=include_ambiguous
        )
    else:
        stats.applied = 0

    return stats


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: --likes URL (mehrfach), --playlist URL (mehrfach), --search, --search-limit N (5),
    --report DATEI, --apply, --include-ambiguous, --track-db PFAD (Standard: cfg["state"]["track_db_path"]),
    -v. Arbeitet mit SoundCloudClient(). Gibt LinkStats per print aus. Rückgabe 0."""
    parser = argparse.ArgumentParser(
        description="Sammlung mit SoundCloud verknüpfen (Likes, Playlists, Suche)."
    )
    parser.add_argument(
        "--likes",
        action="append",
        default=[],
        help="SoundCloud-Likes-URL eines Profils (kann mehrfach angegeben werden)",
    )
    parser.add_argument(
        "--playlist",
        action="append",
        default=[],
        help="SoundCloud-Playlist-URL (kann mehrfach angegeben werden)",
    )
    parser.add_argument(
        "--search",
        action="store_true",
        help="Freie SoundCloud-Suche für nicht gefundene Zeilen nutzen",
    )
    parser.add_argument(
        "--search-limit",
        type=int,
        default=5,
        help="Maximale Trefferanzahl je Suche (Standard: 5)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Pfad für CSV-Report der Vorschläge",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Vorschläge in tracks.sqlite schreiben",
    )
    parser.add_argument(
        "--include-ambiguous",
        action="store_true",
        help="Auch unsichere Vorschläge (Mehrdeutigkeiten, Suchtreffer) schreiben",
    )
    parser.add_argument(
        "--track-db",
        type=Path,
        default=None,
        help="Pfad zu tracks.sqlite (Standard: cfg['state']['track_db_path'])",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Ausführliche Logging-Ausgabe",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    track_db_path = args.track_db
    if track_db_path is None:
        cfg = Config.load("config.yaml")
        track_db_path = Path(cfg["state"]["track_db_path"])

    sc = SoundCloudClient()
    with TrackDB(track_db_path) as db:
        stats = run_link(
            sc,
            db,
            likes=args.likes,
            playlists=args.playlist,
            search=args.search,
            search_limit=args.search_limit,
            apply=args.apply,
            include_ambiguous=args.include_ambiguous,
            report=args.report,
        )

    print(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
