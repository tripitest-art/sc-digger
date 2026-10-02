"""Statusseite: sammelt die Kennzahlen aus State- und Track-Datenbank, ohne zu schreiben."""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import Config
from ..redact import redact
from ..stats import DigestStats, calculate_stats

DEFAULT_MAX_HOURS_SINCE_RUN = 36


@dataclass
class RunInfo:
    finished_at: str        # wie in der runs-Tabelle ("YYYY-MM-DD HH:MM:SS", UTC)
    mode: str
    ok: bool
    found: int
    error: str | None       # bereits mit redact() bereinigt


@dataclass
class StatusSnapshot:
    generated_at: str
    runs: list[RunInfo] = field(default_factory=list)        # letzte 10 Läufe ALLER Modi, neueste zuerst (ORDER BY id DESC)
    last_discover: RunInfo | None = None                    # neuester Lauf mit mode == "discover"
    last_discover_age_hours: float | None = None            # auf 0,1 gerundet
    healthy: bool = False
    alert_active: bool = False                              # health_state: key "alert:discover" == "1"
    inbox_total: int = 0                                    # Zeilen in tracks mit status = 'inbox' (gesamt)
    stats: DigestStats | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-fähig (asdict); enthält alle Felder oben."""
        return asdict(self)


def _connect_readonly(path: str | Path) -> sqlite3.Connection:
    """Öffnet eine SQLite-Datei nur lesend. Fehlt sie, wirft der Aufruf sqlite3.Error."""
    uri = f"file:{Path(path).as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _table_exists(db: sqlite3.Connection, name: str) -> bool:
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _row_to_run(row: tuple) -> RunInfo:
    finished_at, mode, ok, found, error = row
    return RunInfo(
        finished_at=str(finished_at),
        mode=str(mode),
        ok=bool(ok),
        found=int(found) if found is not None else 0,
        error=redact(error) if error else None,
    )


def _read_runs(state_db_path: str | Path) -> list[RunInfo]:
    runs: list[RunInfo] = []
    try:
        with _connect_readonly(state_db_path) as db:
            if not _table_exists(db, "runs"):
                return []
            rows = db.execute(
                "SELECT finished_at, mode, ok, found, error FROM runs ORDER BY id DESC LIMIT 10"
            ).fetchall()
    except sqlite3.Error:
        return []
    runs = [_row_to_run(row) for row in rows]
    return runs


def _read_last_discover(state_db_path: str | Path) -> RunInfo | None:
    try:
        with _connect_readonly(state_db_path) as db:
            if not _table_exists(db, "runs"):
                return None
            row = db.execute(
                "SELECT finished_at, mode, ok, found, error FROM runs "
                "WHERE mode='discover' ORDER BY id DESC LIMIT 1"
            ).fetchone()
    except sqlite3.Error:
        return None
    return _row_to_run(row) if row else None


def _read_alert_active(state_db_path: str | Path) -> bool:
    try:
        with _connect_readonly(state_db_path) as db:
            if not _table_exists(db, "health_state"):
                return False
            row = db.execute(
                "SELECT value FROM health_state WHERE key='alert:discover'"
            ).fetchone()
    except sqlite3.Error:
        return False
    return bool(row and row[0] == "1")


def _read_inbox_total(track_db_path: str | Path) -> int:
    try:
        with _connect_readonly(track_db_path) as db:
            if not _table_exists(db, "tracks"):
                return 0
            row = db.execute(
                "SELECT COUNT(*) FROM tracks WHERE status='inbox'"
            ).fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row else 0


def _age_hours(finished_at: str, now: datetime) -> float | None:
    try:
        parsed = datetime.strptime(finished_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    return round((now - parsed).total_seconds() / 3600.0, 1)


def collect_status(cfg: Config, *, days: int = 7, now: datetime | None = None) -> StatusSnapshot:
    """Liest cfg["state"]["db_path"] (runs, health_state) und cfg["state"]["track_db_path"] (tracks).

    - now: Standard datetime.now(timezone.utc); alle Zeiten der DB sind UTC.
    - healthy = last_discover.ok und Alter <= cfg["health"]["max_hours_since_run"] (Standard 36)
      und kein aktiver Alarm; ohne Lauf False.
    - stats = calculate_stats(track_db, state_db, days=days). calculate_stats legt fehlende Dateien über
      sqlite3.connect an: deshalb nur aufrufen, wenn BEIDE Dateien existieren, sonst DigestStats(days=days).
    - Datenbanken nur lesend öffnen (sqlite3.connect("file:...?mode=ro", uri=True)); fehlt eine Datei,
      ist sie defekt oder fehlt eine Tabelle: Nullen/leere Listen, KEINE Exception, KEINE neue Datei.
    - Fehlertexte aus der DB laufen noch einmal durch sc_digger.redact.redact().
    """
    if now is None:
        now = datetime.now(timezone.utc)

    state_db_path = cfg["state"]["db_path"]
    track_db_path = cfg["state"]["track_db_path"]

    runs = _read_runs(state_db_path)
    last_discover = _read_last_discover(state_db_path)
    alert_active = _read_alert_active(state_db_path)
    inbox_total = _read_inbox_total(track_db_path)

    age = _age_hours(last_discover.finished_at, now) if last_discover else None

    max_hours = float(
        (cfg.raw.get("health") or {}).get("max_hours_since_run", DEFAULT_MAX_HOURS_SINCE_RUN)
    )
    healthy = bool(
        last_discover
        and last_discover.ok
        and age is not None
        and age <= max_hours
        and not alert_active
    )

    if Path(state_db_path).exists() and Path(track_db_path).exists():
        stats = calculate_stats(track_db_path, state_db_path, days=days)
    else:
        stats = DigestStats(days=days)

    return StatusSnapshot(
        generated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        runs=runs,
        last_discover=last_discover,
        last_discover_age_hours=age,
        healthy=healthy,
        alert_active=alert_active,
        inbox_total=inbox_total,
        stats=stats,
    )
