"""Heute-Liste der Startseite (Issue #601)."""
from __future__ import annotations

import logging
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from sc_digger.models import Config
from sc_digger.web.database import _connect_readonly, _table_exists, query_digest

logger = logging.getLogger(__name__)

UNRATED_HREF = "/digest?status=open&feedback=none"
GATES_HREF = "/digest?download_kind=gate&status=open"
WATCH_HREF = "/kaufliste#watch"


@dataclass(frozen=True)
class TodayItem:
    key: str     # "unrated" | "gates" | "watch"
    count: int   # > 0
    label: str   # siehe unten
    href: str    # UNRATED_HREF | GATES_HREF | WATCH_HREF


def _count_watch(track_db_path: str | Path | None) -> int:
    if not track_db_path:
        return 0
    path = Path(track_db_path)
    if not path.is_file():
        return 0
    with closing(_connect_readonly(path)) as db:
        if not _table_exists(db, "availability_watch"):
            return 0
        if not _table_exists(db, "tracks"):
            row = db.execute(
                "SELECT COUNT(*) FROM availability_watch "
                "WHERE available_at IS NOT NULL AND available_at != ''"
            ).fetchone()
        else:
            row = db.execute(
                "SELECT COUNT(*) FROM availability_watch "
                "WHERE available_at IS NOT NULL AND available_at != '' "
                "AND sc_id NOT IN (SELECT sc_id FROM tracks WHERE sc_id IS NOT NULL)"
            ).fetchone()
        return int(row[0]) if row else 0


def collect_today(cfg: Config) -> list[TodayItem]:
    """Offene Aufgaben in fester Reihenfolge unrated, gates, watch; nur Einträge mit count > 0.

    - unrated: query_digest(cfg["state"]["db_path"], cfg["state"]["track_db_path"],
      inbox_dir=cfg.raw["download"]["inbox_dir"] (falls vorhanden), status_filter="open",
      feedback="none", limit=1).total
    - gates: dasselbe mit status_filter="open", download_kind="gate"
    - watch: Zeilen in availability_watch (track_db) mit available_at NOT NULL und != '',
      deren sc_id in keiner Zeile von tracks steht
    - Beschriftung: "1 Vorschlag unbewertet" / "<n> Vorschläge unbewertet",
      "1 offenes Gate" / "<n> offene Gates", "1 Track neu verfügbar" / "<n> Tracks neu verfügbar"
    - Datenbanken nur lesend; fehlt eine Datei oder Tabelle: Eintrag entfällt, KEINE neue Datei.
    - Wirft nie. Scheitert eine Quelle, entfällt nur ihr Eintrag; Log-Meldung auf WARNING:
      "Heute-Liste: Quelle <key> nicht lesbar (<Ausnahmetyp>)" (ohne Pfade, ohne Fehlertext).
    """
    items: list[TodayItem] = []

    inbox_dir: str | Path | None = None
    try:
        if isinstance(cfg.raw, dict):
            download = cfg.raw.get("download")
            if isinstance(download, dict):
                inbox_dir = download.get("inbox_dir")
    except Exception:
        inbox_dir = None

    # 1. unrated
    try:
        state_db = cfg["state"]["db_path"]
        track_db = cfg["state"]["track_db_path"]
        unrated_count = query_digest(
            state_db,
            track_db,
            inbox_dir=inbox_dir,
            status_filter="open",
            feedback="none",
            limit=1,
        ).total
        if unrated_count > 0:
            label = "1 Vorschlag unbewertet" if unrated_count == 1 else f"{unrated_count} Vorschläge unbewertet"
            items.append(TodayItem(key="unrated", count=unrated_count, label=label, href=UNRATED_HREF))
    except Exception as exc:
        logger.warning("Heute-Liste: Quelle %s nicht lesbar (%s)", "unrated", type(exc).__name__)

    # 2. gates
    try:
        state_db = cfg["state"]["db_path"]
        track_db = cfg["state"]["track_db_path"]
        gates_count = query_digest(
            state_db,
            track_db,
            inbox_dir=inbox_dir,
            status_filter="open",
            download_kind="gate",
            limit=1,
        ).total
        if gates_count > 0:
            label = "1 offenes Gate" if gates_count == 1 else f"{gates_count} offene Gates"
            items.append(TodayItem(key="gates", count=gates_count, label=label, href=GATES_HREF))
    except Exception as exc:
        logger.warning("Heute-Liste: Quelle %s nicht lesbar (%s)", "gates", type(exc).__name__)

    # 3. watch
    try:
        track_db = cfg["state"]["track_db_path"]
        watch_count = _count_watch(track_db)
        if watch_count > 0:
            label = "1 Track neu verfügbar" if watch_count == 1 else f"{watch_count} Tracks neu verfügbar"
            items.append(TodayItem(key="watch", count=watch_count, label=label, href=WATCH_HREF))
    except Exception as exc:
        logger.warning("Heute-Liste: Quelle %s nicht lesbar (%s)", "watch", type(exc).__name__)

    return items
