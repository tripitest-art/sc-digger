"""Smartlists-Seite und M3U8-Export (Issue #654).

Liefert gespeicherte Smartlists mit Regel- und Trefferzahl für das Web-Dashboard
sowie M3U8-Playlists passender Tracks.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, cast

from ..db import TrackDB, TrackRecord
from ..smartlists import parse_rules, select_tracks
from .database import _connect_readonly, _table_exists

log = logging.getLogger(__name__)

DEFAULT_PROFILE = "schranz"


@dataclass(frozen=True)
class SmartlistRow:
    name: str
    rule_count: int
    match_count: int


class _ReadOnlyTrackDB:
    """Hülle mit db und profile_id für select_tracks."""

    def __init__(self, connection: sqlite3.Connection, profile_id: str = DEFAULT_PROFILE) -> None:
        self.db = connection
        self.profile_id = profile_id


def collect_smartlists(
    track_db_path: str | Path,
    *,
    profile_id: str = DEFAULT_PROFILE,
) -> list[SmartlistRow]:
    """Gespeicherte Listen des Profils, alphabetisch ohne Rücksicht auf Groß/klein.

    rule_count = Anzahl gesetzter Regelschlüssel im gespeicherten JSON,
    match_count = len(select_tracks(...)) für diese Regeln (0 bei kaputten Regeln).
    Fehlende Datei, fehlende Tabelle `smartlists`, unlesbare Verbindung oder sqlite3.Error
    -> [] (kein Fehler nach außen, WARNING ohne Pfad und ohne Fehlertext).
    """
    try:
        with closing(_connect_readonly(track_db_path)) as db:
            if not _table_exists(db, "smartlists"):
                log.warning("Smartlists nicht lesbar")
                return []
            rows = db.execute(
                "SELECT name, rules_json FROM smartlists WHERE profile_id = ? "
                "ORDER BY name COLLATE NOCASE ASC, name ASC",
                (profile_id,),
            ).fetchall()
            wrapper = _ReadOnlyTrackDB(db, profile_id)
            result: list[SmartlistRow] = []
            for r in rows:
                name = str(r["name"])
                try:
                    rules_dict = json.loads(r["rules_json"]) if r["rules_json"] else {}
                    if not isinstance(rules_dict, dict):
                        rules_dict = {}
                    rule_count = len(rules_dict)
                except Exception:
                    rules_dict = {}
                    rule_count = 0

                try:
                    parsed = parse_rules(rules_dict)
                    tracks = select_tracks(cast(TrackDB, wrapper), parsed)
                    match_count = len(tracks)
                except Exception:
                    match_count = 0

                result.append(
                    SmartlistRow(name=name, rule_count=rule_count, match_count=match_count)
                )
            return result
    except (sqlite3.Error, OSError):
        log.warning("Smartlists nicht lesbar")
        return []


def tracks_for_smartlist(
    track_db_path: str | Path,
    name: str,
    *,
    profile_id: str = DEFAULT_PROFILE,
    limit: int | None = None,
) -> list[TrackRecord] | None:
    """Treffer der gespeicherten Liste (select_tracks, gleiche Sortierung).
    None bei fehlender Track-DB oder unbekannter Liste, [] bei bekannter Liste ohne Treffer."""
    try:
        with closing(_connect_readonly(track_db_path)) as db:
            if not _table_exists(db, "smartlists"):
                return None
            row = db.execute(
                "SELECT rules_json FROM smartlists WHERE profile_id = ? AND name = ?",
                (profile_id, name),
            ).fetchone()
            if row is None:
                return None
            try:
                rules_dict = json.loads(row["rules_json"]) if row["rules_json"] else {}
                if not isinstance(rules_dict, dict):
                    rules_dict = {}
                parsed = parse_rules(rules_dict)
            except Exception:
                return []
            wrapper = _ReadOnlyTrackDB(db, profile_id)
            try:
                return select_tracks(cast(TrackDB, wrapper), parsed, limit=limit)
            except Exception:
                return []
    except (sqlite3.Error, OSError):
        return None


def smartlist_m3u8(tracks: Sequence[TrackRecord]) -> str:
    """Zeile `#EXTM3U`, danach je Track `str(track.path)`, Abschluss mit Zeilenumbruch."""
    lines = ["#EXTM3U"]
    for track in tracks:
        lines.append(str(track.path))
    return "\n".join(lines) + "\n"
