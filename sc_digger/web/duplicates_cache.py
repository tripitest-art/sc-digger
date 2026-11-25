"""Zwischenspeicher für die Duplikat-Gruppen der Web-Seite (Issue #734).

Der Fingerprint-Vergleich aller Track-Paare dauert auf dem Server über eine Minute. Die Gruppen ändern sich
aber nur, wenn sich Fingerprints ändern. Deshalb liegt das Ergebnis als JSON neben der State-DB, zusammen mit
einer Signatur der Fingerprint-Zeilen. Gleiche Signatur: sofortige Antwort. Andere Signatur: der letzte Stand
kommt sofort (``stale``), die Neuberechnung läuft im Hintergrund.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading

from .database import _connect_readonly, _table_exists, query_duplicate_groups

log = logging.getLogger(__name__)

CACHE_NAME = "duplicate_groups.json"

_SIGNATURE_SQL = (
    "SELECT COUNT(*), COALESCE(MAX(id), 0), COALESCE(MAX(updated_at), '') FROM tracks "
    "WHERE fingerprint IS NOT NULL AND fingerprint != '' AND fingerprint_duration IS NOT NULL"
)

# Nur ein Hintergrund-Job je Zwischenspeicher-Datei, sonst rechnen parallele Seitenaufrufe doppelt
_lock = threading.Lock()
_running: set[str] = set()


@dataclass
class DuplicateResult:
    groups: list[dict]          # Format wie query_duplicate_groups
    computed_at: str | None     # UTC "YYYY-MM-DD HH:MM:SS" der Berechnung
    stale: bool                 # True: Fingerprints haben sich geändert, Neuberechnung läuft


def cache_path_for(state_db_path: str | Path) -> Path:
    """<Ordner von state_db_path>/duplicate_groups.json"""
    return Path(state_db_path).parent / CACHE_NAME


def fingerprint_signature(track_db_path: str | Path) -> str | None:
    """Signatur der Fingerprint-Zeilen; nur lesend, ``None`` bei fehlender Datei/Tabelle oder SQLite-Fehler."""
    try:
        with closing(_connect_readonly(track_db_path)) as db:
            if not _table_exists(db, "tracks"):
                return None
            count, max_id, max_upd = db.execute(_SIGNATURE_SQL).fetchone()
        return f"{count or 0}|{max_id or 0}|{max_upd or ''}"
    except (sqlite3.Error, OSError):
        return None


def _read_cache(cache_path: Path) -> dict | None:
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
        return None
    return data


def _write_cache(cache_path: Path, signature: str | None, groups: list[dict]) -> str:
    """Schreibt atomar (.tmp + os.replace); Schreibfehler werden nur geloggt. Gibt den Zeitstempel zurück."""
    computed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    tmp = cache_path.with_name(cache_path.name + ".tmp")
    try:
        tmp.write_text(
            json.dumps({"signature": signature, "computed_at": computed_at, "groups": groups}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, cache_path)
    except OSError as exc:
        log.warning("Duplikat-Zwischenspeicher nicht geschrieben: %s", exc)
    return computed_at


def _start_thread(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="duplicates-cache", daemon=True).start()


def cached_duplicate_groups(
    track_db_path: str | Path,
    cache_path: str | Path,
    *,
    compute: Callable[[str | Path], list[dict]] | None = None,
    background: Callable[[Callable[[], None]], None] | None = None,
) -> DuplicateResult:
    """Duplikat-Gruppen aus dem Zwischenspeicher; rechnet nur bei fehlendem oder veraltetem Stand neu."""
    compute = compute or query_duplicate_groups
    background = background or _start_thread
    cache_path = Path(cache_path)
    signature = fingerprint_signature(track_db_path)
    cached = _read_cache(cache_path)

    if cached is None:
        groups = compute(track_db_path)
        computed_at = _write_cache(cache_path, signature, groups)
        return DuplicateResult(groups=groups, computed_at=computed_at, stale=False)

    if cached.get("signature") == signature:
        return DuplicateResult(groups=cached["groups"], computed_at=cached.get("computed_at"), stale=False)

    key = str(cache_path)
    with _lock:
        start = key not in _running
        if start:
            _running.add(key)
    if start:
        def job() -> None:
            try:
                sig = fingerprint_signature(track_db_path)
                _write_cache(cache_path, sig, compute(track_db_path))
            except Exception:  # noqa: BLE001 - der Hintergrund-Job darf die Seite nie stören
                log.exception("Duplikat-Neuberechnung fehlgeschlagen")
            finally:
                with _lock:
                    _running.discard(key)

        try:
            background(job)
        except Exception:  # noqa: BLE001
            with _lock:
                _running.discard(key)
            log.exception("Duplikat-Neuberechnung nicht gestartet")
    return DuplicateResult(groups=cached["groups"], computed_at=cached.get("computed_at"), stale=True)
