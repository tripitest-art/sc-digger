"""Stillstands-Alarm für die Job-Queue der KI-Workstation (Issue #709).

Wartet ein offener Job (`jobs.status = 'pending'`) seit mindestens `gpu_wake.stale_alert_days`
Tagen, arbeitet die Workstation die Queue nicht ab: Telegram-Alarm und Eintrag in der Alarmliste
der Statusseite (Goldene Regel 7). Läuft täglich im API-Kanarienvogel, bewusst nicht im Weck-Timer:
Läuft der nicht, soll der Alarm trotzdem kommen.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import health
from .models import Config
from .redact import redact

log = logging.getLogger("sc_digger.queue_watch")

MODE = "workstation"
DEFAULT_STALE_DAYS = 3


@dataclass(frozen=True)
class QueueAge:
    pending: int                    # offene Jobs aller Typen (status = 'pending')
    oldest_pending_at: str | None   # created_at des ältesten offenen Jobs (UTC, wie in der DB)
    age_days: float | None          # Alter des ältesten offenen Jobs in Tagen
    stale: bool                     # pending > 0 und age_days >= stale_days


_LEER = QueueAge(0, None, None, False)


def check_queue(track_db_path: str | Path, *, now: datetime,
                stale_days: int = DEFAULT_STALE_DAYS) -> QueueAge:
    """Liest die Tabelle jobs nur lesend (mode=ro, legt nie eine Datei an). now: UTC, naiv oder mit tzinfo.

    Fehlende Datei, fehlende Tabelle oder SQLite-Fehler -> QueueAge(0, None, None, False). Wirft nie.
    """
    try:
        path = Path(track_db_path)
        if not path.exists():
            return _LEER
        con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            pending, oldest = con.execute(
                "SELECT COUNT(*), MIN(created_at) FROM jobs WHERE status = 'pending'"
            ).fetchone()
        finally:
            con.close()
        if not pending or oldest is None:
            return QueueAge(int(pending or 0), None, None, False)
        if now.tzinfo is not None:
            now = now.astimezone(timezone.utc).replace(tzinfo=None)
        created = datetime.strptime(str(oldest)[:19], "%Y-%m-%d %H:%M:%S")
        age_days = (now - created).total_seconds() / 86400
        return QueueAge(int(pending), str(oldest), age_days, age_days >= stale_days)
    except Exception as exc:  # noqa: BLE001 - Wächter darf den Kanarienvogel nie stören
        log.warning("Job-Queue nicht lesbar: %s", redact(str(exc)))
        return _LEER


def run_queue_watch(cfg: Config, *, now: datetime,
                    notify: Callable[[str], None] | None = None) -> int:
    """Prüft die Queue, protokolliert den Lauf (Modus `workstation`) und meldet Alarm bzw. Entwarnung.

    Rückgabe 1 bei Stillstand, sonst 0. Fehlt state.track_db_path oder state.db_path: nichts tun, 0.
    """
    state = cfg.raw.get("state") or {}
    track_db = state.get("track_db_path")
    db_path = state.get("db_path")
    if not track_db or not db_path:
        return 0
    stale_days = int((cfg.raw.get("gpu_wake") or {}).get("stale_alert_days") or DEFAULT_STALE_DAYS)
    q = check_queue(track_db, now=now, stale_days=stale_days)
    error = None
    if q.stale:
        error = (f"{q.pending} Job(s) warten seit {int(q.age_days or 0)} Tagen, "
                 "die Workstation arbeitet sie nicht ab")
    text = health.record_job(db_path, MODE, ok=not q.stale, found=q.pending,
                             error=error, threshold=1)
    if text and notify is not None:
        try:
            notify(text)
        except Exception as exc:  # noqa: BLE001
            log.warning("Workstation-Alarm konnte nicht gesendet werden: %s", redact(str(exc)))
    return 1 if q.stale else 0
