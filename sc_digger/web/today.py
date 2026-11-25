"""Heute-Liste der Startseite (Issue #601)."""
from __future__ import annotations

import logging
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sc_digger.models import Config
from sc_digger.web.database import _connect_readonly, _table_exists, query_digest

logger = logging.getLogger(__name__)

UNRATED_HREF = "/digest?status=open&feedback=none"
GATES_HREF = "/digest?download_kind=gate&status=open"
WATCH_HREF = "/kaufliste#watch"
LATER_HREF = "/digest?status=open&feedback=later"


@dataclass(frozen=True)
class TodayItem:
    key: str     # "unrated" | "gates" | "watch" | "later"
    count: int   # > 0
    label: str   # siehe unten
    href: str    # UNRATED_HREF | GATES_HREF | WATCH_HREF
    new: int = 0  # davon neu seit dem letzten Besuch; 0 ohne since (Issue #710)


# Cookie mit dem Besuchsstand. Ein Besuch endet nach VISIT_GAP_S ohne Aufruf der Startseite,
# damit die "neu"-Zahl beim Auto-Refresh und beim Hin- und Herklicken nicht wegspringt.
VISIT_COOKIE = "scd_besuch"
VISIT_GAP_S = 1800
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def _parse_ts(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, _TS_FORMAT)
    except (ValueError, TypeError):
        return None


def next_visit_state(cookie: str | None, now: datetime) -> tuple[str | None, str]:
    """Cookie-Format "<vorheriger Besuch>|<letzter Aufruf>", beide UTC "YYYY-MM-DD HH:MM:SS" oder leer.

    now: naive UTC-Zeit. Unlesbarer oder fehlender Cookie zählt wie kein Cookie.
    Liegt der letzte Aufruf >= VISIT_GAP_S zurück, wird er zum vorherigen Besuch.
    Rückgabe: (since = vorheriger Besuch oder None, neuer Cookie-Wert "<vorheriger>|<now>"). Wirft nie.
    """
    try:
        jetzt = now.strftime(_TS_FORMAT)
        previous: str | None = None
        last: str | None = None
        if cookie and cookie.count("|") == 1:
            raw_prev, raw_last = cookie.split("|")
            if raw_prev == "" or _parse_ts(raw_prev) is not None:
                if raw_last == "" or _parse_ts(raw_last) is not None:
                    previous = raw_prev or None
                    last = raw_last or None
        last_dt = _parse_ts(last) if last else None
        if last_dt is not None and now - last_dt >= timedelta(seconds=VISIT_GAP_S):
            previous = last
        return previous, f"{previous or ''}|{jetzt}"
    except Exception:
        return None, f"|{now:%Y-%m-%d %H:%M:%S}"


def _count_watch(track_db_path: str | Path | None, since: str | None = None) -> int:
    if not track_db_path:
        return 0
    path = Path(track_db_path)
    if not path.is_file():
        return 0
    with closing(_connect_readonly(path)) as db:
        if not _table_exists(db, "availability_watch"):
            return 0
        neu, args = (" AND available_at > ?", [since]) if since else ("", [])
        if not _table_exists(db, "tracks"):
            row = db.execute(
                "SELECT COUNT(*) FROM availability_watch "
                "WHERE available_at IS NOT NULL AND available_at != ''" + neu,
                args,
            ).fetchone()
        else:
            row = db.execute(
                "SELECT COUNT(*) FROM availability_watch "
                "WHERE available_at IS NOT NULL AND available_at != '' "
                "AND sc_id NOT IN (SELECT sc_id FROM tracks WHERE sc_id IS NOT NULL)" + neu,
                args,
            ).fetchone()
        return int(row[0]) if row else 0


def collect_today(cfg: Config, *, since: str | None = None) -> list[TodayItem]:
    """Offene Aufgaben in fester Reihenfolge unrated, gates, watch, later; nur Einträge mit count > 0.

    - unrated: query_digest(cfg["state"]["db_path"], cfg["state"]["track_db_path"],
      inbox_dir=cfg.raw["download"]["inbox_dir"] (falls vorhanden), status_filter="open",
      feedback="none", limit=1).total
    - gates: dasselbe mit status_filter="open", download_kind="gate"
    - watch: Zeilen in availability_watch (track_db) mit available_at NOT NULL und != '',
      deren sc_id in keiner Zeile von tracks steht
    - later: dasselbe mit feedback="later" (mit ⏳ gemerkt), Beschriftung "<n> für später gemerkt"
    - Beschriftung: "1 Vorschlag unbewertet" / "<n> Vorschläge unbewertet",
      "1 offenes Gate" / "<n> offene Gates", "1 Track neu verfügbar" / "<n> Tracks neu verfügbar"
    - since (Issue #710): je Eintrag zusätzlich new = Zahl davon, die seit since neu ist
      (Digest: seen.reported_at > since, watch: available_at > since); ohne since new = 0.
    - Datenbanken nur lesend; fehlt eine Datei oder Tabelle: Eintrag entfällt, KEINE neue Datei.
    - Wirft nie. Scheitert eine Quelle, entfällt nur ihr Eintrag; Log-Meldung auf WARNING:
      "Heute-Liste: Quelle <key> nicht lesbar (<Ausnahmetyp>)" (ohne Pfade, ohne Fehlertext).
    """
    items: list[TodayItem] = []

    def neu_im_digest(**filters: str) -> int:
        # Eine Fehlerquelle für die Zusatzzahl darf den Eintrag selbst nicht kippen.
        if not since:
            return 0
        try:
            return query_digest(state_db, track_db, inbox_dir=inbox_dir, status_filter="open",
                                limit=1, since=since, **filters).total
        except Exception:
            return 0

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
            items.append(TodayItem(key="unrated", count=unrated_count, label=label, href=UNRATED_HREF,
                                   new=neu_im_digest(feedback="none")))
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
            items.append(TodayItem(key="gates", count=gates_count, label=label, href=GATES_HREF,
                                   new=neu_im_digest(download_kind="gate")))
    except Exception as exc:
        logger.warning("Heute-Liste: Quelle %s nicht lesbar (%s)", "gates", type(exc).__name__)

    # 3. watch
    try:
        track_db = cfg["state"]["track_db_path"]
        watch_count = _count_watch(track_db)
        if watch_count > 0:
            label = "1 Track neu verfügbar" if watch_count == 1 else f"{watch_count} Tracks neu verfügbar"
            neu_watch = 0
            if since:
                try:
                    neu_watch = _count_watch(track_db, since)
                except Exception:
                    neu_watch = 0
            items.append(TodayItem(key="watch", count=watch_count, label=label, href=WATCH_HREF,
                                   new=neu_watch))
    except Exception as exc:
        logger.warning("Heute-Liste: Quelle %s nicht lesbar (%s)", "watch", type(exc).__name__)

    # 4. later
    try:
        state_db = cfg["state"]["db_path"]
        track_db = cfg["state"]["track_db_path"]
        later_count = query_digest(
            state_db,
            track_db,
            inbox_dir=inbox_dir,
            status_filter="open",
            feedback="later",
            limit=1,
        ).total
        if later_count > 0:
            items.append(TodayItem(key="later", count=later_count, label=f"{later_count} für später gemerkt",
                                   href=LATER_HREF, new=neu_im_digest(feedback="later")))
    except Exception as exc:
        logger.warning("Heute-Liste: Quelle %s nicht lesbar (%s)", "later", type(exc).__name__)

    return items
