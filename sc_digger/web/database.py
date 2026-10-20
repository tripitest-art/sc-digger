"""Abfrage-Schicht für den Track-Explorer des Dashboards (Issue #220, #229).

Die lesenden Abfragen öffnen die Track-Datenbank ausschließlich im Modus ``ro`` (kein
Anlegen, kein Schreiben) und liefern zusammengefasste Kennzahlen
(:func:`get_collection_overview`) sowie gefilterte Track-Listen (:func:`query_tracks`).
Fehlt die Datei oder das Schema, kommen leere Ergebnisse zurück statt einer Exception –
die Statusoberfläche darf an einem fehlenden Index nie scheitern.

Seit Issue #229 gibt es genau einen schreibenden Pfad, :func:`save_track_feedback`: er
schreibt ein Feedback in ``sc_feedback`` und wirft nie (Rückgabe ``False`` bei ungültigem
Wert oder DB-Fehler).
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ..collection import NameIndex, NameKey, name_key, normalize, remixer_tokens
from ..cues import apply_cue_result
from ..db import FEEDBACK_VALUES, JobRecord, TrackDB
from ..dj_export import Entry
from ..linksource import normalize_url

_AUDIO_EXT = {".wav", ".aiff", ".aif", ".flac", ".mp3", ".m4a"}

# Spalten, die ein Suchtreffer an die Oberfläche liefert (bewusst ohne Audio-Fingerprint
# und Qualitätsdetails; die gehören nicht in eine Listenansicht).
_TRACK_COLUMNS = (
    "id", "path", "artist", "title", "format", "bitrate_kbps", "cutoff_hz",
    "quality_status", "bpm", "key_camelot", "key_name", "status", "adopted_at",
    "feedback", "source_url", "created_at", "updated_at", "rb_play_count",
    "breakdown_ratio",
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


def _sc_id_from_path(path: str | None) -> int | None:
    """SoundCloud-ID aus dem Dateinamen ``[<sc_id>] Artist - Titel.ext``.

    Inbox und Sammlung benennen Dateien mit der SoundCloud-ID in eckigen Klammern
    (siehe ``output.find_in_inbox``). Nur dieser Präfix zählt, damit Pfadbestandteile wie
    BPM-Ordner keine Zahl liefern. Kein Präfix -> ``None``. Wirft nie.
    """
    if not path:
        return None
    match = re.match(r"\[(\d+)\]", Path(path).name)
    return int(match.group(1)) if match else None


def _feedback_values(db: sqlite3.Connection, paths: list[str]) -> dict[int, str]:
    """Aktuelle ``sc_feedback``-Werte zu den SoundCloud-IDs der übergebenen Dateipfade.

    Rein lesend. Fehlt die Tabelle oder schlägt die Abfrage fehl, kommt ein leeres Ergebnis
    zurück – die Track-Liste soll an einem fehlenden Feedback-Index nicht scheitern.
    """
    ids = [sc for sc in (_sc_id_from_path(p) for p in paths) if sc is not None]
    if not ids:
        return {}
    try:
        if not _table_exists(db, "sc_feedback"):
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = db.execute(
            f"SELECT sc_id, value FROM sc_feedback WHERE sc_id IN ({placeholders})", ids
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {int(row["sc_id"]): str(row["value"] or "") for row in rows}


def _snapshot_values(db: sqlite3.Connection, sc_ids: list[int]) -> dict[int, dict[str, int]]:
    """Neueste Engagement-Zahlen je SoundCloud-ID aus ``track_snapshots`` (Issue #258).

    Rein lesend. Ein Track kann mehrere Snapshots haben; je ``sc_id`` zählt der jüngste
    (``recorded_at``, bei Gleichstand die höchste ``id``). Fehlt die Tabelle oder schlägt die
    Abfrage fehl, kommt ein leeres Ergebnis zurück – der Track-Explorer soll an einem fehlenden
    Snapshot-Index nicht scheitern.
    """
    ids = sorted(set(sc_ids))
    if not ids:
        return {}
    try:
        if not _table_exists(db, "track_snapshots"):
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = db.execute(
            f"SELECT sc_id, plays, likes, reposts, comments FROM track_snapshots "
            f"WHERE sc_id IN ({placeholders}) "
            "ORDER BY sc_id ASC, datetime(recorded_at) DESC, id DESC",
            ids,
        ).fetchall()
    except sqlite3.Error:
        return {}
    latest: dict[int, dict[str, int]] = {}
    for row in rows:
        if row["sc_id"] is None:
            continue
        sid = int(row["sc_id"])
        if sid in latest:
            continue
        latest[sid] = {
            "plays": int(row["plays"] or 0),
            "likes": int(row["likes"] or 0),
            "reposts": int(row["reposts"] or 0),
            "comments": int(row["comments"] or 0),
        }
    return latest


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
    breakdown_ratio_max: float | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> TrackSearchResult:
    """Filtert Tracks aus der Datenbank, sortiert nach ``id`` aufsteigend.

    ``status``/``key_camelot``/``quality_status`` sind exakte Vergleiche, ``search`` ist eine
    Wortsuche über artist/title, ``bpm_min``/``bpm_max`` bilden einen Bereich,
    ``adopted`` filtert auf (nicht) in die Sammlung übernommene Tracks. ``limit`` wird auf
    ``MAX_LIMIT`` begrenzt, ``total`` ist die Zahl aller Treffer unabhängig von ``limit``.
    ``breakdown_ratio_max`` (Issue #334) behält nur Tracks mit bekanntem Breakdown-Anteil bis
    zu diesem Wert; ``NULL`` fällt heraus, weil ein unbekannter Wert kein Peaktime-Werkzeug
    belegt.
    Zusätzlich enthält jeder Treffer ``sc_id`` (aus dem Dateinamen ``[<sc_id>]``, sonst
    ``None``) und ``sc_feedback`` (aktueller ``sc_feedback``-Wert, sonst ``None``), damit
    die Oberfläche die Bewertung (Issue #229) anzeigen und ändern kann. Ebenso kommen die
    neuesten SoundCloud-Engagement-Zahlen ``sc_plays``/``sc_likes``/``sc_reposts``/
    ``sc_comments`` aus ``track_snapshots`` (Issue #258; ``None``, wenn keine SC-ID oder kein
    Snapshot vorliegt), ``rb_play_count`` (Rekordbox-Abspielzähler) und ``breakdown_ratio``
    (Anteil kickloser Zeit, ``None`` wenn nicht analysiert) mit.
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
    if breakdown_ratio_max is not None:
        where.append("breakdown_ratio IS NOT NULL AND breakdown_ratio <= ?")
        params.append(float(breakdown_ratio_max))
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
            # Ältere Track-DBs (vor Migration 7) kennen breakdown_ratio nicht; die rein
            # lesende Abfrage migriert nicht und wählt deshalb nur vorhandene Spalten.
            available = {
                row["name"] for row in db.execute("PRAGMA table_info(tracks)").fetchall()
            }
            if breakdown_ratio_max is not None and "breakdown_ratio" not in available:
                return TrackSearchResult()
            total = int(db.execute(
                f"SELECT COUNT(*) FROM tracks{clause_sql}", params
            ).fetchone()[0])
            columns = ", ".join(col for col in _TRACK_COLUMNS if col in available)
            rows = db.execute(
                f"SELECT {columns} FROM tracks{clause_sql} "
                "ORDER BY id ASC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            paths = [str(row["path"] or "") for row in rows]
            feedback_map = _feedback_values(db, paths)
            snapshot_map = _snapshot_values(
                db,
                [sc for sc in (_sc_id_from_path(p) for p in paths) if sc is not None],
            )
    except sqlite3.Error:
        return TrackSearchResult()

    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item.setdefault("breakdown_ratio", None)
        sc_id = _sc_id_from_path(item.get("path"))
        item["sc_id"] = sc_id
        item["sc_feedback"] = feedback_map.get(sc_id) if sc_id is not None else None
        snapshot = snapshot_map.get(sc_id) if sc_id is not None else None
        item["sc_plays"] = snapshot["plays"] if snapshot else None
        item["sc_likes"] = snapshot["likes"] if snapshot else None
        item["sc_reposts"] = snapshot["reposts"] if snapshot else None
        item["sc_comments"] = snapshot["comments"] if snapshot else None
        items.append(item)
    return TrackSearchResult(total=total, items=items)


# --------------------------------------------------------------- Set-Builder (Issue #340)


def _set_builder_entries(
    track_db_path: str | Path,
    *,
    bpm_min: float | None = None,
    bpm_max: float | None = None,
    status: str | None = None,
    track_ids: list[int] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[tuple[int, Entry]]:
    """Track-Zeilen samt ``id`` als :class:`~sc_digger.dj_export.Entry`-Paare.

    Nur lesend (``mode=ro``), sortiert nach ``id``. Tracks ohne BPM oder ohne Camelot-Key
    werden übersprungen: der Set-Builder kann sie nicht einordnen, und ``order_harmonic``
    würde sie nur als Lücken mitschleppen. ``track_ids`` filtert bereits in SQL, damit die
    Auswahl nicht am ``limit`` hängt; eine leere Liste bedeutet bewusst „keine Auswahl".
    Fehlt die Datei/Tabelle, kommt eine leere Liste zurück (wirft nie).
    """
    limit = max(0, min(int(limit), MAX_LIMIT))
    if track_ids is not None:
        ids = sorted({int(track_id) for track_id in track_ids})
        if not ids:
            return []
    else:
        ids = []

    where = ["bpm IS NOT NULL", "TRIM(key_camelot) != ''"]
    params: list[Any] = []
    if bpm_min is not None:
        where.append("bpm >= ?")
        params.append(float(bpm_min))
    if bpm_max is not None:
        where.append("bpm <= ?")
        params.append(float(bpm_max))
    if status:
        where.append("status = ?")
        params.append(status)
    if ids:
        where.append(f"id IN ({','.join('?' for _ in ids)})")
        params.extend(ids)
    params.append(limit)

    try:
        with closing(_connect_readonly(track_db_path)) as db:
            if not _table_exists(db, "tracks"):
                return []
            available = {
                row["name"] for row in db.execute("PRAGMA table_info(tracks)").fetchall()
            }
            if not {"bpm", "key_camelot"} <= available:
                return []
            rows = db.execute(
                "SELECT id, path, artist, title, bpm, key_camelot, key_name "
                f"FROM tracks WHERE {' AND '.join(where)} ORDER BY id ASC LIMIT ?",
                params,
            ).fetchall()
    except sqlite3.Error:
        return []

    entries: list[tuple[int, Entry]] = []
    for row in rows:
        path = str(row["path"] or "")
        if not path:
            continue
        entries.append((
            int(row["id"]),
            Entry(
                path=Path(path),
                artist=str(row["artist"] or ""),
                title=str(row["title"] or ""),
                bpm=float(row["bpm"]) if row["bpm"] is not None else None,
                camelot=str(row["key_camelot"] or "").strip() or None,
                key_name=row["key_name"],
            ),
        ))
    return entries


def load_tracks_for_set_builder(
    track_db_path: str | Path,
    *,
    bpm_min: float | None = None,
    bpm_max: float | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[Entry]:
    """Liest Tracks rein lesend aus TrackDB als Entry-Objekte für sc_digger.dj_export.

    Filtert nach bpm_min/max und status (archive/inbox), ignoriert Tracks ohne BPM/Key.
    ``limit`` wird auf :data:`MAX_LIMIT` begrenzt; fehlt die Datenbank, ist das Ergebnis leer.
    """
    return [
        entry
        for _, entry in _set_builder_entries(
            track_db_path,
            bpm_min=bpm_min,
            bpm_max=bpm_max,
            status=status,
            limit=limit,
        )
    ]


@dataclass
class StoreItemResult:
    """Ein offener Store-Track (Bandcamp/Beatport) samt aktueller Bewertung."""

    sc_id: int
    title: str
    artist: str
    purchase_url: str
    purchase_title: str | None
    first_seen: str
    last_seen: str
    feedback: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StoreSearchResult:
    """Eine Seite der Kaufliste samt Gesamtzahl der Treffer."""

    total: int = 0
    items: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def query_store_items(
    track_db_path: str | Path,
    *,
    search: str | None = None,
    feedback: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> StoreSearchResult:
    """Liest Store-Items rein lesend aus store_items mit LEFT JOIN auf sc_feedback.

    Sortiert nach ``store_items.last_seen`` DESC, ``store_items.id`` DESC. ``search`` sucht
    wie im Digest als Wortsuche über Artist und Titel, ``feedback`` filtert exakt auf
    ``like``/``dislike``/``later`` (``none``: noch ohne Bewertung). ``limit`` wird auf
    ``MAX_LIMIT`` begrenzt, ``total`` zählt alle Treffer unabhängig von ``limit``.
    Fehlt die Datei oder eine Tabelle, kommt ein leeres Ergebnis zurück; die Funktion wirft nie.
    """
    limit = max(0, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))

    try:
        with closing(_connect_readonly(track_db_path)) as db:
            if not _table_exists(db, "store_items"):
                return StoreSearchResult()
            has_feedback = _table_exists(db, "sc_feedback")
            join = (
                " LEFT JOIN sc_feedback ON sc_feedback.sc_id = store_items.sc_id"
                if has_feedback else ""
            )
            feedback_column = (
                "sc_feedback.value AS feedback" if has_feedback else "NULL AS feedback"
            )

            where: list[str] = []
            params: list[Any] = []
            if search:
                clause, extra = _like_clause(search)
                if clause:
                    where.append(f"({clause})")
                    params.extend(extra)
            if feedback:
                fb = feedback.strip().lower()
                if not has_feedback:
                    # Ohne sc_feedback-Tabelle gibt es keine Bewertungen: „none“ trifft
                    # alle Store-Items, jeder andere Filter keinen einzigen.
                    if fb != "none":
                        return StoreSearchResult()
                elif fb == "none":
                    where.append("sc_feedback.value IS NULL")
                elif fb in FEEDBACK_VALUES:
                    where.append("sc_feedback.value = ?")
                    params.append(fb)

            clause_sql = f" WHERE {' AND '.join(where)}" if where else ""
            total = int(db.execute(
                f"SELECT COUNT(*) FROM store_items{join}{clause_sql}", params
            ).fetchone()[0])
            rows = db.execute(
                "SELECT store_items.sc_id, store_items.title, store_items.artist, "
                "store_items.purchase_url, store_items.purchase_title, "
                f"store_items.first_seen, store_items.last_seen, {feedback_column} "
                f"FROM store_items{join}{clause_sql} "
                "ORDER BY store_items.last_seen DESC, store_items.id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
    except sqlite3.Error:
        return StoreSearchResult()

    items = [
        StoreItemResult(
            sc_id=int(row["sc_id"]),
            title=str(row["title"] or ""),
            artist=str(row["artist"] or ""),
            purchase_url=str(row["purchase_url"] or ""),
            purchase_title=row["purchase_title"],
            first_seen=str(row["first_seen"] or ""),
            last_seen=str(row["last_seen"] or ""),
            feedback=row["feedback"],
        ).to_dict()
        for row in rows
    ]
    return StoreSearchResult(total=total, items=items)


@dataclass
class DigestItem:
    """Repräsentiert einen im Digest oder On-Demand vorgeschlagenen Track."""

    sc_id: int
    url: str
    title: str
    artist: str | None
    reported_at: str
    download_kind: str  # 'native', 'gate', 'store', 'stream'
    gate_url: str | None = None
    purchase_url: str | None = None
    feedback: str | None = None  # 'like', 'dislike', 'later'
    in_collection: bool = False
    in_inbox: bool = False
    plays: int = 0
    likes: int = 0
    reposts: int = 0
    comments: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DigestSearchResult:
    """Suchergebnis für die Digest-Historie."""

    total: int = 0
    items: list[DigestItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"total": self.total, "items": [i.to_dict() for i in self.items]}


def _parse_artist_title(raw: str) -> tuple[str | None, str]:
    """Trennt 'Artist - Title' oder 'Artist – Title' sauber; sonst (None, raw)."""
    for sep in (" - ", " – "):
        if sep in raw:
            parts = raw.split(sep, 1)
            return parts[0].strip(), parts[1].strip()
    return None, raw.strip()


def query_digest(
    state_db_path: str | Path,
    track_db_path: str | Path,
    *,
    inbox_dir: str | Path | None = None,
    search: str = "",
    status_filter: str = "",  # '', 'collection', 'inbox', 'open'
    download_kind: str = "",  # '', 'native', 'gate', 'store', 'stream'
    feedback: str = "",       # '', 'like', 'dislike', 'later', 'none'
    limit: int = 50,
    offset: int = 0,
) -> DigestSearchResult:
    """Liest chronologisch (neueste zuerst) aus seen.sqlite (seen-Tabelle) und joint
    Informationen aus track_db (tracks, sc_feedback, store_items, gate_items).
    Gleicht den Status (in_inbox, in_collection) primär per SoundCloud-ID ([<sc_id>],
    zusätzlich über die Spalte tracks.sc_id für per Fingerprint übernommene Sammlungsdateien)
    und als Fallback per Fuzzy-Match auf Artist & Title (unter Beachtung von Remixer-Tokens)
    ab. Zusätzlich zählt die in ``tracks.source_url`` gespeicherte SoundCloud-URL (Issue #307,
    Vergleichsform über ``linksource.normalize_url``): gleiche URL heißt vorhanden, auch wenn
    die Datei anders heißt. Ist inbox_dir übergeben und vorhanden, werden dortige Audio-Dateien
    ebenfalls berücksichtigt.
    Fehlt eine DB-Datei oder Tabelle, wirft die Funktion nicht, sondern liefert leere/partielle Ergebnisse.
    Nur lesend; limit wird auf max. 200 begrenzt.
    """
    state_path = Path(state_db_path)
    if not state_path.is_file():
        return DigestSearchResult()

    feedback_map: dict[int, str] = {}
    store_map: dict[int, str] = {}
    gate_map: dict[int, str] = {}
    snapshot_map: dict[int, dict[str, int]] = {}
    inbox_ids: set[int] = set()
    archive_ids: set[int] = set()
    # Vergleichsformen der gespeicherten SoundCloud-URLs je Status (Issue #307). Leere URLs
    # werden nicht aufgenommen, sonst würde ein Eintrag ohne URL über "" fälschlich passen.
    inbox_urls: set[str] = set()
    archive_urls: set[str] = set()
    # Inbox-Dateien und DB-Zeilen landen im selben Namens-Index; abgefragt wird mit der
    # gemeinsamen Logik aus collection.names_match (früher ein eigener Fuzzy-Matcher).
    inbox_index = NameIndex()
    archive_index = NameIndex()
    # Handkorrekturen des Matchers (Issue #325): sie gehen jedem automatischen Treffer vor.
    match_overrides: dict[int, str] = {}

    if inbox_dir:
        inbox_p = Path(inbox_dir)
        if inbox_p.is_dir():
            for p in inbox_p.rglob("*"):
                if not p.is_file() or p.suffix.lower() not in _AUDIO_EXT:
                    continue
                try:
                    rel = p.relative_to(inbox_p)
                except ValueError:
                    continue
                # Dateien unter "<Inbox>/_rejected/duplicate/" wurden geladen und als Dublette
                # einer Sammlungsdatei aussortiert; sie zählen deshalb zur Sammlung (Issue #301).
                # Nur dieser Präfix ist erlaubt, alles andere mit "_"/"." bleibt ausgeschlossen.
                parts = rel.parts
                dup_prefix = len(parts) >= 3 and parts[0] == "_rejected" and parts[1] == "duplicate"
                rest = parts[2:] if dup_prefix else parts
                if any(part.startswith(("_", ".")) for part in rest):
                    continue
                m = re.match(r"^\[(\d+)\]", p.name)
                if m:
                    if dup_prefix:
                        archive_ids.add(int(m.group(1)))
                    else:
                        inbox_ids.add(int(m.group(1)))
                stem = re.sub(r"^\[\d+\]\s*", "", p.stem)
                file_key = name_key(None, stem)
                if file_key.tokens:
                    if dup_prefix:
                        archive_index.add(str(p), file_key)
                    else:
                        inbox_index.add(str(p), file_key)

    track_path = Path(track_db_path)
    if track_path.is_file():
        try:
            with closing(_connect_readonly(track_path)) as tdb:
                if _table_exists(tdb, "sc_feedback"):
                    for row in tdb.execute("SELECT sc_id, value FROM sc_feedback"):
                        if row["sc_id"] is not None:
                            feedback_map[int(row["sc_id"])] = str(row["value"] or "")
                if _table_exists(tdb, "store_items"):
                    for row in tdb.execute("SELECT sc_id, purchase_url FROM store_items"):
                        if row["sc_id"] is not None and row["purchase_url"]:
                            store_map[int(row["sc_id"])] = str(row["purchase_url"])
                if _table_exists(tdb, "gate_items"):
                    for row in tdb.execute("SELECT sc_id, gate_url FROM gate_items"):
                        if row["sc_id"] is not None and row["gate_url"]:
                            gate_map[int(row["sc_id"])] = str(row["gate_url"])
                if _table_exists(tdb, "tracks"):
                    cols = {col["name"] for col in tdb.execute("PRAGMA table_info(tracks)").fetchall()}
                    has_artist_title = "artist" in cols and "title" in cols
                    # Ältere Tabellen ohne source_url bleiben unterstützt (Issue #307).
                    has_source_url = "source_url" in cols
                    # Ältere DBs ohne sc_id (Migration 14) behalten das bisherige Verhalten.
                    has_sc_id = "sc_id" in cols
                    for row in tdb.execute("SELECT * FROM tracks"):
                        p_str = str(row["path"] or "")
                        st_str = str(row["status"] or "").lower()
                        row_url = normalize_url(row["source_url"]) if has_source_url else ""
                        # Die per Fingerprint übertragene sc_id erkennt Sammlungsdateien
                        # auch ohne [<id>]-Präfix (ROADMAP 3.12); sie gilt vor dem Namensabgleich.
                        row_sc_id = row["sc_id"] if has_sc_id else None
                        if row_sc_id is not None:
                            if st_str == "inbox":
                                inbox_ids.add(int(row_sc_id))
                            elif st_str in ("archive", "adopted"):
                                archive_ids.add(int(row_sc_id))
                        m = re.search(r"\[(\d+)\]", p_str)
                        if m:
                            tid = int(m.group(1))
                            if st_str == "inbox":
                                inbox_ids.add(tid)
                            elif st_str in ("archive", "adopted"):
                                archive_ids.add(tid)
                        # Die URL zählt unabhängig von der SoundCloud-ID im Dateinamen; nur
                        # inbox/archive/adopted und nicht-leere URLs werden aufgenommen.
                        if row_url and st_str == "inbox":
                            inbox_urls.add(row_url)
                        elif row_url and st_str in ("archive", "adopted"):
                            archive_urls.add(row_url)
                        artist = str(row["artist"] or "") if has_artist_title and "artist" in row.keys() else ""
                        title = str(row["title"] or "") if has_artist_title and "title" in row.keys() else ""
                        if not artist and not title and p_str:
                            stem = re.sub(r"^\[\d+\]\s*", "", Path(p_str).stem)
                            row_key = name_key(None, stem)
                        else:
                            row_key = name_key(artist, title)
                        if not row_key.tokens:
                            continue
                        if st_str == "inbox":
                            inbox_index.add(p_str, row_key)
                        elif st_str in ("archive", "adopted"):
                            archive_index.add(p_str, row_key)
                # Handkorrekturen des Matchers (Issue #325). Ältere DBs ohne die Migration
                # bleiben unterstützt, dann wirkt nur der automatische Abgleich.
                if _table_exists(tdb, "match_overrides"):
                    for row in tdb.execute("SELECT sc_id, override_type FROM match_overrides"):
                        if row["sc_id"] is not None:
                            match_overrides[int(row["sc_id"])] = str(row["override_type"] or "")
                # Neuesten Engagement-Snapshot je Track ergänzen (Issue #257). Die Abfrage steht
                # zuletzt, damit eine minimale Fremd-Tabelle ohne diese Spalten die übrigen Maps
                # nicht verwirft; ORDER BY liefert je sc_id den jüngsten Snapshot zuerst.
                if _table_exists(tdb, "track_snapshots"):
                    for row in tdb.execute(
                        "SELECT sc_id, plays, likes, reposts, comments FROM track_snapshots "
                        "ORDER BY sc_id ASC, datetime(recorded_at) DESC, id DESC"
                    ):
                        if row["sc_id"] is None:
                            continue
                        sid = int(row["sc_id"])
                        if sid in snapshot_map:
                            continue
                        snapshot_map[sid] = {
                            "plays": int(row["plays"] or 0),
                            "likes": int(row["likes"] or 0),
                            "reposts": int(row["reposts"] or 0),
                            "comments": int(row["comments"] or 0),
                        }
        except sqlite3.Error:
            pass

    try:
        with closing(_connect_readonly(state_path)) as sdb:
            if not _table_exists(sdb, "seen"):
                return DigestSearchResult()
            rows = sdb.execute(
                "SELECT id, url, title, reported_at FROM seen ORDER BY reported_at DESC, id DESC"
            ).fetchall()
    except sqlite3.Error:
        return DigestSearchResult()

    items: list[DigestItem] = []
    for r in rows:
        sc_id = int(r["id"])
        raw_title = str(r["title"] or "")
        artist, title = _parse_artist_title(raw_title)
        url = str(r["url"] or "")
        reported_at = str(r["reported_at"] or "")

        full_title = f"{artist} {title}".strip() if artist else title
        # Remixer-Tokens nur aus dem Titel: ein Klammer-Zusatz im Artist-Namen ist kein Remixer.
        q_key = NameKey(
            tokens=frozenset(normalize(full_title)),
            remix=frozenset(remixer_tokens(title)),
        )

        # Die gespeicherte URL des Eintrags in Vergleichsform: trifft sie eine tracks-Zeile
        # mit passendem Status, gilt der Track als vorhanden – unabhängig vom Dateinamen.
        digest_url = normalize_url(url)
        in_inbox = (
            (sc_id in inbox_ids)
            or (digest_url in inbox_urls)
            or inbox_index.find(q_key) is not None
        )
        in_coll = (
            (sc_id in archive_ids)
            or (digest_url in archive_urls)
            or archive_index.find(q_key) is not None
        )
        # Handkorrektur des Matchers schlägt alle automatischen Treffer (Issue #325).
        override = match_overrides.get(sc_id)
        if override == "already_in_collection":
            in_coll = True
        elif override == "not_duplicate":
            in_coll = False

        purchase_url = store_map.get(sc_id)
        gate_url = gate_map.get(sc_id)
        if purchase_url:
            kind = "store"
        elif gate_url:
            kind = "gate"
        elif in_inbox or in_coll:
            kind = "native"
        else:
            kind = "stream"

        snap = snapshot_map.get(sc_id, {})
        item = DigestItem(
            sc_id=sc_id,
            url=url,
            title=title,
            artist=artist,
            reported_at=reported_at,
            download_kind=kind,
            purchase_url=purchase_url,
            gate_url=gate_url if kind == "gate" else None,
            feedback=feedback_map.get(sc_id),
            in_collection=in_coll,
            in_inbox=in_inbox,
            plays=snap.get("plays", 0),
            likes=snap.get("likes", 0),
            reposts=snap.get("reposts", 0),
            comments=snap.get("comments", 0),
        )

        if search:
            s_low = search.strip().lower()
            t_low = item.title.lower()
            a_low = (item.artist or "").lower()
            raw_low = raw_title.lower()
            if s_low not in t_low and s_low not in a_low and s_low not in raw_low:
                continue

        if status_filter:
            st = status_filter.strip().lower()
            if st == "collection" and not item.in_collection:
                continue
            elif st == "inbox" and not item.in_inbox:
                continue
            elif st == "open" and (item.in_collection or item.in_inbox):
                continue

        if download_kind:
            dk = download_kind.strip().lower()
            if item.download_kind != dk:
                continue

        if feedback:
            fb = feedback.strip().lower()
            if fb == "none":
                if item.feedback is not None:
                    continue
            elif item.feedback != fb:
                continue

        items.append(item)

    limit = max(0, min(int(limit), 200))
    offset = max(0, int(offset))
    total = len(items)
    paged_items = items[offset : offset + limit] if limit > 0 else []

    return DigestSearchResult(total=total, items=paged_items)


def save_track_feedback(
    track_db_path: str | Path,
    sc_id: int,
    value: str,
) -> bool:
    """Speichert das Feedback eines Tracks in ``sc_feedback`` der Track-DB.

    Gültige Werte: ``'like'``, ``'dislike'``, ``'later'``. Letzte Wahl gewinnt (Upsert je
    SoundCloud-ID), ``updated_at`` wird auf jetzt gesetzt. Gibt ``True`` bei Erfolg zurück,
    ``False`` bei ungültigem Wert, fehlender Datei/Tabelle oder DB-Fehler – wirft nie.

    Die Verbindung wird im Modus ``rw`` geöffnet: ein unbekannter Pfad legt so keine leere
    Datenbank an, sondern führt zu ``False`` (gleiches Prinzip wie die lesenden Abfragen).
    """
    if value not in FEEDBACK_VALUES:
        return False
    try:
        sc_id_int = int(sc_id)
    except (TypeError, ValueError):
        return False
    try:
        uri = f"file:{Path(track_db_path).as_posix()}?mode=rw"
        with closing(sqlite3.connect(uri, uri=True)) as db:
            db.execute(
                """
                INSERT INTO sc_feedback (sc_id, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(sc_id) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (sc_id_int, value),
            )
            db.commit()
    except sqlite3.Error:
        return False
    return True


# --------------------------------------------------------------- Job-Queue (Issue #330)

_FINISH_STATUSES = ("completed", "failed")


def _job_value(value: Any) -> Any:
    """Enum-Feld (z. B. ``JobStatus.RUNNING``) als reine Zeichenkette für JSON."""
    return value.value if isinstance(value, Enum) else value


def _job_to_dict(job: JobRecord) -> dict[str, Any]:
    """JobRecord als JSON-fähiges Dict für die REST-Antwort der Worker-API."""
    return {
        "id": job.id,
        "track_id": job.track_id,
        "job_type": _job_value(job.job_type),
        "status": _job_value(job.status),
        "payload": job.payload,
        "result": job.result,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def claim_job_for_worker(
    track_db_path: str | Path, job_type: str | None = None
) -> dict[str, Any] | None:
    """Beansprucht atomar den nächsten offenen Job (``pending`` -> ``running``).

    Nutzt ``TrackDB.claim_next_job``; ohne ``job_type`` zählt jeder Typ. Fehlt die Datei
    oder schlägt die Abfrage fehl, kommt ``None`` zurück statt einer Exception – ein
    externer Worker soll an einer fehlenden DB nicht scheitern. Eine fehlende Datei wird
    dabei nicht angelegt (``TrackDB`` würde das tun).
    """
    if not Path(track_db_path).is_file():
        return None
    try:
        with TrackDB(track_db_path) as db:
            job = db.claim_next_job(job_type)
    except (sqlite3.Error, OSError):
        return None
    return _job_to_dict(job) if job is not None else None


def heartbeat_job(track_db_path: str | Path, job_id: int) -> bool:
    """Aktualisiert das Lebenszeichen eines laufenden Jobs (``updated_at``).

    Gibt ``True`` nur zurück, wenn genau ein Job im Status ``running`` aktualisiert wurde.
    Wirft nie; fehlende Datei oder DB-Fehler ergeben ``False``.
    """
    if not Path(track_db_path).is_file():
        return False
    try:
        with TrackDB(track_db_path) as db:
            return db.touch_job(int(job_id))
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return False


def finish_job(
    track_db_path: str | Path,
    job_id: int,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    *,
    inbox_dir: str | Path | None = None,
) -> bool:
    """Setzt das Ergebnis eines Jobs auf ``completed`` oder ``failed``.

    Bei ``completed`` und ``job_type == 'cues'`` übernimmt ``apply_cue_result`` die
    Cue-Punkte in die Track-DB und schreibt die Tags für Inbox-Dateien (Sammlung
    bleibt unberührt). ``inbox_dir`` ist dafür die erlaubte Schreibwurzel.

    Gibt ``True`` bei Erfolg zurück, ``False`` bei unbekanntem Status, unbekanntem Job,
    fehlender Datei oder DB-Fehler. Wirft nie.
    """
    if status not in _FINISH_STATUSES:
        return False
    if not Path(track_db_path).is_file():
        return False
    try:
        with TrackDB(track_db_path) as db:
            job = db.get_job_by_id(int(job_id))
            if job is None:
                return False
            db.update_job(int(job_id), status, result=result, error=error)
            if status == "completed" and _job_value(job.job_type) == "cues":
                apply_cue_result(db, int(job_id), result or {}, inbox_dir=inbox_dir)
            updated = db.get_job_by_id(int(job_id))
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return False
    return updated is not None and _job_value(updated.status) == status

