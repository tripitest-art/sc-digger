"""Rein lesende Abfrage-Schicht für den Track-Explorer des Dashboards (Issue #220).

Öffnet die Track-Datenbank ausschließlich im Modus ``ro`` (kein Anlegen, kein Schreiben)
und liefert zusammengefasste Kennzahlen (:func:`get_collection_overview`) sowie gefilterte
Track-Listen (:func:`query_tracks`). Fehlt die Datei oder das Schema, kommen leere
Ergebnisse zurück statt einer Exception – die Statusoberfläche darf an einem fehlenden
Index nie scheitern.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Spalten, die ein Suchtreffer an die Oberfläche liefert (bewusst ohne Audio-Fingerprint
# und Qualitätsdetails; die gehören nicht in eine Listenansicht).
_TRACK_COLUMNS = (
    "id", "path", "artist", "title", "format", "bitrate_kbps", "cutoff_hz",
    "quality_status", "bpm", "key_camelot", "key_name", "status", "adopted_at",
    "feedback", "source_url", "created_at", "updated_at",
)

# Schutz vor versehentlich riesigen Antworten; das Frontend blättert über offset.
DEFAULT_LIMIT = 100
MAX_LIMIT = 500


@dataclass
class CollectionOverview:
    """Kennzahlen über die gesamte Track-Datenbank."""

    total_tracks: int = 0
    archive_count: int = 0
    inbox_count: int = 0
    rejected_count: int = 0
    adopted_count: int = 0                                   # adopted_at IS NOT NULL
    quality_counts: dict[str, int] = field(default_factory=dict)
    format_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-fähig (asdict); alle Felder oben."""
        return asdict(self)


@dataclass
class TrackSearchResult:
    """Eine Seite gefilterter Tracks samt Gesamtzahl der Treffer."""

    total: int = 0
    items: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Öffnet eine SQLite-Datei nur lesend mit Zeilen als ``sqlite3.Row``.

    Fehlt die Datei, wirft der Aufruf ``sqlite3.Error``. Der Aufrufer schließt die
    Verbindung (``contextlib.closing``).
    """
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    return db


def _table_exists(db: sqlite3.Connection, name: str) -> bool:
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _counts_by(db: sqlite3.Connection, column: str) -> dict[str, int]:
    """Gruppiert eine Spalte und zählt die Zeilen; leere/NULL-Werte werden übersprungen."""
    rows = db.execute(
        f"SELECT {column}, COUNT(*) FROM tracks GROUP BY {column}"
    ).fetchall()
    return {str(key): int(count) for key, count in rows if key not in (None, "")}


def get_collection_overview(db_path: str | Path) -> CollectionOverview:
    """Liefert Gesamtzahlen, Status-, Qualitäts- und Format-Verteilung der Sammlung.

    Nur lesend; fehlt die Datei, die Tabelle oder bricht die Abfrage ab, kommt ein leeres
    :class:`CollectionOverview` zurück (keine Exception, keine neue Datei).
    """
    try:
        with closing(_connect_readonly(db_path)) as db:
            if not _table_exists(db, "tracks"):
                return CollectionOverview()
            total = int(db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0])
            status_counts = _counts_by(db, "status")
            quality_counts = _counts_by(db, "quality_status")
            format_counts = _counts_by(db, "format")
            adopted = int(db.execute(
                "SELECT COUNT(*) FROM tracks WHERE adopted_at IS NOT NULL"
            ).fetchone()[0])
    except sqlite3.Error:
        return CollectionOverview()

    return CollectionOverview(
        total_tracks=total,
        archive_count=status_counts.get("archive", 0),
        inbox_count=status_counts.get("inbox", 0),
        rejected_count=status_counts.get("rejected", 0),
        adopted_count=adopted,
        quality_counts=quality_counts,
        format_counts=format_counts,
    )


def _like_clause(search: str) -> tuple[str, list[Any]]:
    """Baut eine parametrisierte Wortsuche auf artist/title; ``%`` und ``_`` werden escaped.

    Mehrere Wörter müssen alle vorkommen (UND), damit ``svetec raw`` den Track
    ``Svetec – Raw`` findet. Gleiche Semantik wie ``TrackDB.search_tracks``.
    """
    clauses: list[str] = []
    params: list[Any] = []
    for word in str(search).split():
        escaped = (
            word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        clauses.append("(artist LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\')")
        params.extend((f"%{escaped}%", f"%{escaped}%"))
    return " AND ".join(clauses), params


def query_tracks(
    db_path: str | Path,
    *,
    status: str | None = None,
    search: str | None = None,
    bpm_min: float | None = None,
    bpm_max: float | None = None,
    key_camelot: str | None = None,
    quality_status: str | None = None,
    adopted: bool | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> TrackSearchResult:
    """Filtert Tracks aus der Datenbank, sortiert nach ``id`` aufsteigend.

    ``status``/``key_camelot``/``quality_status`` sind exakte Vergleiche, ``search`` ist eine
    Wortsuche über artist/title, ``bpm_min``/``bpm_max`` bilden einen Bereich,
    ``adopted`` filtert auf (nicht) in die Sammlung übernommene Tracks. ``limit`` wird auf
    ``MAX_LIMIT`` begrenzt, ``total`` ist die Zahl aller Treffer unabhängig von ``limit``.
    Nur lesend; fehlt die Datei/Tabelle, kommt ein leeres Ergebnis zurück.
    """
    where: list[str] = []
    params: list[Any] = []

    if status:
        where.append("status = ?")
        params.append(status)
    if quality_status:
        where.append("quality_status = ?")
        params.append(quality_status)
    if key_camelot:
        where.append("key_camelot = ?")
        params.append(key_camelot)
    if bpm_min is not None:
        where.append("bpm >= ?")
        params.append(float(bpm_min))
    if bpm_max is not None:
        where.append("bpm <= ?")
        params.append(float(bpm_max))
    if adopted is True:
        where.append("adopted_at IS NOT NULL")
    elif adopted is False:
        where.append("adopted_at IS NULL")
    if search:
        clause, extra = _like_clause(search)
        if clause:
            where.append(f"({clause})")
            params.extend(extra)

    clause_sql = f" WHERE {' AND '.join(where)}" if where else ""
    limit = max(0, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))

    try:
        with closing(_connect_readonly(db_path)) as db:
            if not _table_exists(db, "tracks"):
                return TrackSearchResult()
            total = int(db.execute(
                f"SELECT COUNT(*) FROM tracks{clause_sql}", params
            ).fetchone()[0])
            columns = ", ".join(_TRACK_COLUMNS)
            rows = db.execute(
                f"SELECT {columns} FROM tracks{clause_sql} "
                "ORDER BY id ASC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
    except sqlite3.Error:
        return TrackSearchResult()

    return TrackSearchResult(total=total, items=[dict(row) for row in rows])
