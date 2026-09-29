"""Retry-Queue für fehlgeschlagene Original-Downloads.

Downloads von Originalen können durch temporäre Fehler (Netzwerk-Timeout,
SoundCloud-Schluckauf, scdl-Abbruch) fehlschlagen. Diese Queue speichert
fehlgeschlagene native Downloads in der State-DB und wiederholt sie beim
nächsten discover-Lauf bis zu max_attempts-mal.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Track
from .redact import redact

log = logging.getLogger(__name__)


@dataclass
class RetryItem:
    sc_id: int
    url: str
    title: str
    artist: str
    attempts: int
    last_error: str | None


class RetryQueue:
    def __init__(self, db_path: str | Path, max_attempts: int = 3):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_attempts = max_attempts
        self.db = sqlite3.connect(str(self.db_path), timeout=30.0)
        self.db.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS download_retries ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " sc_id INTEGER UNIQUE NOT NULL,"
            " url TEXT NOT NULL,"
            " title TEXT NOT NULL,"
            " artist TEXT NOT NULL,"
            " attempts INTEGER NOT NULL DEFAULT 0,"
            " last_error TEXT,"
            " status TEXT NOT NULL DEFAULT 'pending',"
            " created_at TEXT DEFAULT CURRENT_TIMESTAMP,"
            " updated_at TEXT DEFAULT CURRENT_TIMESTAMP"
            ");"
        )
        self.db.commit()

    def __enter__(self) -> "RetryQueue":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.db.close()

    def record_failure(self, t: Track | RetryItem | Any, error: str) -> str:
        """Zählt einen Fehlversuch (legt den Eintrag beim ersten Mal an: sc_id, url, title, artist).
        Rückgabe "pending", solange attempts < max_attempts, sonst "failed".
        error wird durch redact() bereinigt und auf 300 Zeichen gekürzt gespeichert."""
        sc_id = getattr(t, "id", None) if hasattr(t, "id") else getattr(t, "sc_id", None)
        if sc_id is None:
            raise ValueError(f"Ungültiger Track/Item ohne ID: {t}")
        url = getattr(t, "url", "")
        title = getattr(t, "title", "")
        artist = getattr(t, "artist", "")
        error_str = "" if error is None else str(error)
        cleaned_error = redact(error_str)[:300]

        row = self.db.execute(
            "SELECT attempts FROM download_retries WHERE sc_id = ?", (sc_id,)
        ).fetchone()

        if row is None:
            attempts = 1
            status = "pending" if attempts < self.max_attempts else "failed"
            self.db.execute(
                "INSERT INTO download_retries (sc_id, url, title, artist, attempts, last_error, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (sc_id, url, title, artist, attempts, cleaned_error, status),
            )
        else:
            attempts = row["attempts"] + 1
            status = "pending" if attempts < self.max_attempts else "failed"
            self.db.execute(
                "UPDATE download_retries SET attempts = ?, last_error = ?, status = ?, updated_at = CURRENT_TIMESTAMP WHERE sc_id = ?",
                (attempts, cleaned_error, status, sc_id),
            )
        self.db.commit()
        return status

    def record_success(self, sc_id: int) -> bool:
        """Entfernt den Eintrag. True, wenn er existierte."""
        cur = self.db.execute("DELETE FROM download_retries WHERE sc_id = ?", (sc_id,))
        self.db.commit()
        return cur.rowcount > 0

    def due(self) -> list[RetryItem]:
        """Alle Einträge mit Status "pending", älteste zuerst."""
        rows = self.db.execute(
            "SELECT sc_id, url, title, artist, attempts, last_error "
            "FROM download_retries WHERE status = 'pending' "
            "ORDER BY id ASC"
        ).fetchall()
        return [
            RetryItem(
                sc_id=r["sc_id"],
                url=r["url"],
                title=r["title"],
                artist=r["artist"],
                attempts=r["attempts"],
                last_error=r["last_error"],
            )
            for r in rows
        ]

    def status(self, sc_id: int) -> str | None:
        """"pending" | "failed" | None (kein Eintrag)."""
        row = self.db.execute(
            "SELECT status FROM download_retries WHERE sc_id = ?", (sc_id,)
        ).fetchone()
        return row["status"] if row is not None else None
