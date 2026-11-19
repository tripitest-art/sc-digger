"""Lebenslauf eines Tracks: welche Stationen er erreicht hat (Teil 1 von #590, Issue #615).

Die Seite `/track/sc/<sc_id>` beantwortet „wo ist Track X gerade, und warum?“, ohne dass man
Digest, Quarantäne, Kaufliste und Track-Explorer einzeln absuchen muss. Alles wird nur lesend
aus den vorhandenen Tabellen zusammengesetzt; es gibt bewusst keine Migration und keine neuen
Zeitstempel (Entscheidung von Stephan in #590). Fehlt eine Quelle, ist die Station schlicht
nicht erreicht.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import Config

log = logging.getLogger(__name__)

# Reihenfolge ist die Reihenfolge auf der Seite (dict behält die Einfügereihenfolge).
STEP_LABELS: dict[str, str] = {
    "found": "Entdeckt",
    "rated": "Bewertet",
    "gate": "Gate gefunden",
    "store": "Im Store",
    "rejected": "Quarantäne",
    "inbox": "In der Inbox",
    "analyzed": "Analysiert",
    "archive": "In der Sammlung",
    "adopted": "Übernommen",
}

FEEDBACK_LABELS = {"like": "gefällt", "dislike": "passt nicht", "later": "später"}


@dataclass(frozen=True)
class LifeStep:
    key: str
    label: str
    at: str | None
    detail: str | None


@dataclass(frozen=True)
class TrackLife:
    sc_id: int
    artist: str | None
    title: str
    url: str | None
    steps: tuple[LifeStep, ...]

    @property
    def current(self) -> str:
        """Schlüssel der letzten erreichten Station."""
        return self.steps[-1].key


def _open_ro(path: Any) -> sqlite3.Connection | None:
    """Öffnet eine SQLite-Datei nur lesend; fehlt sie, None statt sie anzulegen.

    `mode=ro` über eine URI, weil ein normales `sqlite3.connect` eine fehlende Datei still
    erzeugen würde – die Seite darf auf dem Server nichts anlegen.
    """
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        conn = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection | None, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Fehlende Tabelle oder Spalte zählt als „keine Zeile“, nicht als Fehler."""
    if conn is None:
        return []
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.Error:
        return []


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _earliest(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Bei mehreren Profilen (gate_items/store_items je Profil) zählt der früheste Fund.

    Sortiert in Python statt per ORDER BY, damit eine fehlende Spalte `first_seen` die
    Abfrage nicht scheitern lässt.
    """
    if not rows:
        return None
    return min(rows, key=lambda r: (_text(r.get("first_seen")) is None, str(r.get("first_seen") or ""),
                                    r.get("id") or 0))


def _split_title(raw: Any) -> tuple[str | None, str | None]:
    """„Artist - Titel“ aus seen/rejected in Artist und Titel trennen (erstes „ - “)."""
    text = _text(raw)
    if text is None:
        return None, None
    if " - " in text:
        artist, title = text.split(" - ", 1)
        return _text(artist), _text(title) or text
    return None, text


def _utc_text(mtime: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(mtime), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _bpm_text(bpm: Any) -> str | None:
    try:
        value = float(bpm)
    except (TypeError, ValueError):
        return None
    return f"{round(value, 1):g}"


def _pick_track_row(by_id: list[dict[str, Any]], by_path: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Wählt die eine Zeile der Track-DB, aus der Inbox/Analyse/Sammlung/Übernahme stammen.

    Nach einer Übernahme tragen Inbox-Zeile (mit `adopted_at`) und Archivzeile dieselbe
    `sc_id` (`mark_adopted`). Die übernommene Zeile erzählt den vollständigeren Lebenslauf,
    deshalb geht sie vor; ein Treffer über `sc_id` geht vor einem Treffer über den Dateinamen.
    """
    seen_ids: set[Any] = set()
    candidates: list[tuple[int, dict[str, Any]]] = []
    for source_rank, rows in ((0, by_id), (1, by_path)):
        for row in rows:
            key = row.get("id", id(row))
            if key in seen_ids:
                continue
            seen_ids.add(key)
            candidates.append((source_rank, row))
    if not candidates:
        return None

    def rank(item: tuple[int, dict[str, Any]]) -> tuple:
        source_rank, row = item
        status = row.get("status")
        if _text(row.get("adopted_at")):
            state = 0
        elif status == "inbox":
            state = 1
        elif status == "archive":
            state = 2
        else:
            state = 3
        return (source_rank, state, row.get("id") or 0)

    return min(candidates, key=rank)[1]


def _collect(cfg: Config, sc_id: int) -> TrackLife | None:
    state_cfg = cfg["state"] or {}
    state = _open_ro(state_cfg.get("db_path"))
    tracks = _open_ro(state_cfg.get("track_db_path"))
    try:
        seen = next(iter(_rows(state, "SELECT * FROM seen WHERE id = ?", (sc_id,))), None)
        rejected = next(iter(_rows(state, "SELECT * FROM rejected WHERE id = ?", (sc_id,))), None)
        feedback = next(iter(_rows(tracks, "SELECT * FROM sc_feedback WHERE sc_id = ?", (sc_id,))), None)
        gate = _earliest(_rows(tracks, "SELECT * FROM gate_items WHERE sc_id = ?", (sc_id,)))
        store = _earliest(_rows(tracks, "SELECT * FROM store_items WHERE sc_id = ?", (sc_id,)))
        # Ältere Inbox-Dateien haben noch keine sc_id-Spalte, aber die ID im Dateinamen.
        # instr statt LIKE, weil „[“ und „_“ sonst Musterbedeutung bekommen könnten.
        row = _pick_track_row(
            _rows(tracks, "SELECT * FROM tracks WHERE sc_id = ?", (sc_id,)),
            _rows(tracks, "SELECT * FROM tracks WHERE instr(path, ?) > 0", (f"[{sc_id}]",)),
        )
    finally:
        for conn in (state, tracks):
            if conn is not None:
                conn.close()

    reached: dict[str, tuple[str | None, str | None]] = {}
    if seen is not None:
        reached["found"] = (_text(seen.get("reported_at")), None)
    if feedback is not None:
        value = _text(feedback.get("value"))
        reached["rated"] = (_text(feedback.get("updated_at")), FEEDBACK_LABELS.get(value or "", value))
    if gate is not None:
        reached["gate"] = (_text(gate.get("first_seen")), _text(gate.get("gate_url")))
    if store is not None:
        reached["store"] = (_text(store.get("first_seen")), _text(store.get("purchase_url")))
    if rejected is not None:
        reached["rejected"] = (_text(rejected.get("rejected_at")), _text(rejected.get("reason")))
    if row is not None:
        adopted_at = _text(row.get("adopted_at"))
        status = row.get("status")
        if status == "inbox" or adopted_at:
            reached["inbox"] = (_utc_text(row.get("mtime")), _text(row.get("path")))
        bpm = _bpm_text(row.get("bpm"))
        key = _text(row.get("key_camelot"))
        if bpm and key:
            reached["analyzed"] = (None, f"{bpm} BPM · {key}")
        if status == "archive" and not adopted_at:
            reached["archive"] = (None, _text(row.get("path")))
        if adopted_at:
            reached["adopted"] = (adopted_at, None)

    steps = tuple(
        LifeStep(key=key, label=label, at=reached[key][0], detail=reached[key][1])
        for key, label in STEP_LABELS.items()
        if key in reached
    )
    if not steps:
        return None

    artist, title = None, None
    for source in (seen, rejected):
        if source is not None and title is None:
            artist, title = _split_title(source.get("title"))
    if title is None:
        for source in (gate, store, row):
            if source is not None and _text(source.get("title")):
                artist, title = _text(source.get("artist")), _text(source.get("title"))
                break
    url = None
    for source in (seen, rejected):
        if source is not None and _text(source.get("url")):
            url = _text(source.get("url"))
            break

    return TrackLife(
        sc_id=sc_id,
        artist=artist,
        title=title or f"Track {sc_id}",
        url=url,
        steps=steps,
    )


def collect_track_life(cfg: Config, sc_id: int) -> TrackLife | None:
    """Liest cfg["state"]["db_path"] und cfg["state"]["track_db_path"] nur lesend
    (mode=ro); fehlende Datei, Tabelle oder Spalte zählt als „nicht erreicht“, legt nichts an.
    None, wenn die ID in keiner Quelle steht oder kein int ist. Wirft nie."""
    # bool ist ein int-Untertyp, aber True als SoundCloud-ID wäre ein Aufruferfehler.
    if not isinstance(sc_id, int) or isinstance(sc_id, bool):
        return None
    try:
        return _collect(cfg, sc_id)
    except Exception:  # noqa: BLE001 – die Seite soll nie mit 500 scheitern
        log.warning("Lebenslauf für Track %s konnte nicht gelesen werden", sc_id, exc_info=True)
        return None
