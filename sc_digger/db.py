"""Zentrale Track-Datenbank und asynchrone Job-Queue (Phase 2.1).

Verwaltet Metadaten aller Tracks (Sammlung, Inbox, Rejected) inklusive
Qualitätsurteil, Audio-Messwerten (BPM, Key, LUFS, True Peak), Audio-Fingerprints
sowie Hintergrundaufgaben (für spätere KI-Embeddings auf der Workstation).
Enthält ein versioniertes Migrationssystem zur sicheren Schema-Evolution.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import posixpath
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

log = logging.getLogger(__name__)

STALE_TIMEOUT_MINUTES: dict[str, int] = {"embedding": 120, "caption": 30, "tag_backfill": 30, "fingerprint": 30}


class TrackStatus(str, Enum):
    ARCHIVE = "archive"    # Bestehende Master-Sammlung (/music/Schranz)
    INBOX = "inbox"        # Neu heruntergeladene, geprüfte Tracks
    REJECTED = "rejected"  # Abgelehnte Fakes / Brickwall-Clipped


class QualityStatus(str, Enum):
    OK = "ok"                          # Bitrate und Spektrum einwandfrei
    FAKE_TRANSCODE = "fake_transcode"  # Spektrums-Cutoff zu niedrig (z.B. 128k in 320k Container)
    CLIPPED = "clipped"                # Brickwall-Mastering / Dynamikbereich zu gering
    CORRUPT = "corrupt"                # ffprobe / Audio-Parsing fehlgeschlagen
    UNKNOWN = "unknown"                # Noch nicht geprüft


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobType(str, Enum):
    EMBEDDING = "embedding"        # Asynchrones Audio-Embedding (Workstation)
    CAPTION = "caption"            # Audio-Beschreibung / KI-Zusammenfassung
    TAG_BACKFILL = "tag_backfill"  # Opt-in Metadaten-Ergänzung
    FINGERPRINT = "fingerprint"    # Chromaprint-Berechnung


def _normalize_path(p: str | Path) -> str:
    """Reine Zeichenkettenoperation, kein Dateisystemzugriff:
    posixpath.normpath(str(p).replace("\\", "/")).
    Löst "." und ".." auf, doppelte und abschließende "/" verschwinden, Backslashes werden "/".
    Relative Pfade bleiben relativ (bewusst kein abspath: das Ergebnis hinge vom
    Arbeitsverzeichnis ab), Symlinks werden nicht aufgelöst (NFS).
    """
    return posixpath.normpath(str(p).replace("\\", "/"))


@dataclass
class TrackRecord:
    path: str
    mtime: float
    size: int
    id: int | None = None
    format: str | None = None
    bitrate_kbps: float | None = None
    cutoff_hz: int | None = None
    # Default None, damit beim INSERT der SQLite-DEFAULT ('unknown' / 'archive') greift
    # und beim UPDATE bestehende Werte nicht überschrieben werden.
    quality_status: QualityStatus | str | None = None
    quality_details: dict[str, Any] | None = None
    bpm: float | None = None
    bpm_source: str | None = None  # 'audio', 'tag', 'text', 'manual'
    key_camelot: str | None = None
    key_name: str | None = None
    lufs: float | None = None
    true_peak_dbfs: float | None = None
    loudness_range_lu: float | None = None
    artist: str | None = None
    title: str | None = None
    source_url: str | None = None
    fingerprint: str | None = None
    fingerprint_duration: float | None = None
    status: TrackStatus | str | None = None
    feedback: str | None = None  # 'like', 'dislike', 'later'
    created_at: str | None = None
    updated_at: str | None = None

    def __post_init__(self) -> None:
        self.path = _normalize_path(self.path)

    def to_db_dict(self) -> dict[str, Any]:
        """Konvertiert das Datenmodell in serialisierbare SQL-Werte."""
        data = asdict(self)
        if isinstance(self.quality_status, Enum):
            data["quality_status"] = self.quality_status.value
        if isinstance(self.status, Enum):
            data["status"] = self.status.value
        if self.quality_details is not None:
            data["quality_details"] = json.dumps(self.quality_details, ensure_ascii=False)
        return data

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> TrackRecord:
        """Erzeugt ein TrackRecord aus einer SQLite-Ergebniszeile.

        Resistent gegen unbekannte Spalten (z. B. nach Schema-Rollbacks).
        """
        d = dict(row)
        q_det = d.get("quality_details")
        if q_det and isinstance(q_det, str):
            try:
                d["quality_details"] = json.loads(q_det)
            except Exception:
                d["quality_details"] = None
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class JobRecord:
    track_id: int
    job_type: JobType | str
    id: int | None = None
    status: JobStatus | str = JobStatus.PENDING
    payload: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> JobRecord:
        """Erzeugt ein JobRecord aus einer SQLite-Ergebniszeile.

        Resistent gegen unbekannte Spalten.
        """
        d = dict(row)
        payload = d.get("payload")
        if payload and isinstance(payload, str):
            try:
                d["payload"] = json.loads(payload)
            except Exception:
                d["payload"] = None
        result = d.get("result")
        if result and isinstance(result, str):
            try:
                d["result"] = json.loads(result)
            except Exception:
                d["result"] = None
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass(frozen=True)
class ArtistReputation:
    artist: str
    downloads: int = 0  # Tracks in inbox/archive ohne explizites Feedback
    likes: int = 0      # Tracks oder Store-Items mit feedback = 'like'
    dislikes: int = 0   # Tracks oder Store-Items mit feedback = 'dislike'


FEEDBACK_VALUES: tuple[str, ...] = ("like", "dislike", "later")


# ================================================================= Migrationen

MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "0001_initial_track_db",
        """
        CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE NOT NULL,
            mtime REAL NOT NULL,
            size INTEGER NOT NULL,
            format TEXT,
            bitrate_kbps REAL,
            cutoff_hz INTEGER,
            quality_status TEXT NOT NULL DEFAULT 'unknown',
            quality_details TEXT,
            bpm REAL,
            bpm_source TEXT,
            key_camelot TEXT,
            key_name TEXT,
            lufs REAL,
            true_peak_dbfs REAL,
            loudness_range_lu REAL,
            artist TEXT,
            title TEXT,
            source_url TEXT,
            fingerprint TEXT,
            fingerprint_duration REAL,
            status TEXT NOT NULL DEFAULT 'archive',
            feedback TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_tracks_path ON tracks(path);
        CREATE INDEX IF NOT EXISTS idx_tracks_status ON tracks(status);
        CREATE INDEX IF NOT EXISTS idx_tracks_fingerprint ON tracks(fingerprint);
        CREATE INDEX IF NOT EXISTS idx_tracks_quality_status ON tracks(quality_status);

        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            payload TEXT,
            result TEXT,
            error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_status_type ON jobs(status, job_type);
        CREATE INDEX IF NOT EXISTS idx_jobs_track_id ON jobs(track_id);
        """,
    ),
    (
        2,
        "0002_sc_feedback",
        """
        CREATE TABLE IF NOT EXISTS sc_feedback (
            sc_id INTEGER PRIMARY KEY,
            url TEXT,
            value TEXT NOT NULL CHECK (value IN ('like', 'dislike', 'later')),
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """,
    ),
    (
        3,
        "0003_store_items",
        """
        CREATE TABLE IF NOT EXISTS store_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sc_id INTEGER UNIQUE NOT NULL,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            purchase_url TEXT NOT NULL,
            purchase_title TEXT,
            first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_store_items_last_seen ON store_items(last_seen);
        """,
    ),
    (
        4,
        "0004_track_snapshots",
        """
        CREATE TABLE IF NOT EXISTS track_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sc_id INTEGER NOT NULL,
            artist TEXT NOT NULL,
            title TEXT,
            plays INTEGER NOT NULL DEFAULT 0,
            likes INTEGER NOT NULL DEFAULT 0,
            reposts INTEGER NOT NULL DEFAULT 0,
            comments INTEGER NOT NULL DEFAULT 0,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_track_snapshots_sc_id ON track_snapshots(sc_id);
        CREATE INDEX IF NOT EXISTS idx_track_snapshots_recorded_at ON track_snapshots(recorded_at);
        """,
    ),
    (
        5,
        "0005_exploration_tag_stats",
        """
        CREATE TABLE IF NOT EXISTS exploration_tag_stats (
            tag TEXT PRIMARY KEY,
            uses INTEGER NOT NULL,
            successes INTEGER NOT NULL,
            last_used_at TEXT,
            last_success_at TEXT
        );
        """,
    ),
]


def _apply_migrations(db: sqlite3.Connection) -> None:
    """Führt unaufgeführte Schema-Migrationen versioniert und atomar transaktional aus."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY,"
        " name TEXT NOT NULL,"
        " applied_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    applied = {
        row[0] for row in db.execute("SELECT version FROM schema_migrations").fetchall()
    }

    for version, name, script in MIGRATIONS:
        if version not in applied:
            try:
                db.executescript(
                    f"BEGIN;\n{script}\n"
                    f"INSERT INTO schema_migrations (version, name) VALUES ({int(version)}, '{name}');\n"
                    f"COMMIT;"
                )
            except Exception:
                if db.in_transaction:
                    db.rollback()
                raise


# ================================================================= TrackDB

class TrackDB:
    """Verwaltet den persistenten Track-Index und Hintergrund-Jobs."""

    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        # WAL-Modus für sicheren gleichzeitigen Lese-/Schreibzugriff (Achtung: nicht auf NFS!)
        self.db.execute("PRAGMA journal_mode=WAL;")
        self.db.execute("PRAGMA foreign_keys=ON;")
        _apply_migrations(self.db)

    def __enter__(self) -> TrackDB:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------ Track-Operationen

    def upsert_track(self, record: TrackRecord) -> TrackRecord:
        """Speichert oder aktualisiert einen Track-Eintrag anhand des Pfads.

        Überschreibt bestehende Felder nur, wenn sie im Record explizit gesetzt sind
        (kein Überschreiben mit None). Feedback wird ausschließlich über set_feedback geändert.
        """
        # Nur nicht-leere Felder aktualisieren, Feedback nie überschreiben
        data = {
            k: v for k, v in record.to_db_dict().items()
            if v is not None and k not in ("id", "created_at", "updated_at", "feedback")
        }
        data["path"] = _normalize_path(data["path"])

        fields = list(data.keys())
        placeholders = ", ".join(f":{f}" for f in fields)
        set_clause = ", ".join(
            f"{f} = excluded.{f}" for f in fields if f != "path"
        )

        query = f"""
            INSERT INTO tracks ({", ".join(fields)})
            VALUES ({placeholders})
            ON CONFLICT(path) DO UPDATE SET
                {set_clause},
                updated_at = CURRENT_TIMESTAMP
            RETURNING *;
        """
        cur = self.db.execute(query, data)
        row = cur.fetchone()
        self.db.commit()
        return TrackRecord.from_row(row)

    def get_track_by_path(self, path: str | Path) -> TrackRecord | None:
        """Sucht einen Track anhand seines normalisierten Dateipfads."""
        p_str = _normalize_path(path)
        row = self.db.execute("SELECT * FROM tracks WHERE path = ?", (p_str,)).fetchone()
        return TrackRecord.from_row(row) if row else None

    def get_track_by_id(self, track_id: int) -> TrackRecord | None:
        row = self.db.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        return TrackRecord.from_row(row) if row else None

    def search_tracks(self, query: str, limit: int = 6) -> list[TrackRecord]:
        """Sucht Tracks zu einem Suchtext. Der Suchtext wird an Leerzeichen in
        Wörter geteilt; ein Track trifft zu, wenn JEDES Wort (ohne
        Groß-/kleinschreibung) in artist ODER title vorkommt – so findet
        "svetec raw" den Track "Svetec – Raw". Die LIKE-Wildcards % und _ werden
        pro Wort mit ESCAPE escaped, damit ein wörtlicher Suchtext ("100%")
        nicht alles matcht; die Abfrage ist parametrisiert.
        Hinweis: SQLite-LIKE ist nur für ASCII groß-/kleinschreibungsunabhängig
        (Umlaute: bewusst nicht Teil dieser Aufgabe).
        Reihenfolge deterministisch: artist, title, id. Maximal limit Treffer.
        Wirft nie (DB-Fehler -> leere Liste mit Log-Warnung)."""
        try:
            words = [w for w in str(query).split() if w]
            if not words:
                return []
            conds: list[str] = []
            params: list[Any] = []
            for word in words:
                escaped = (
                    word.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                conds.append("(artist LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\')")
                params.extend((f"%{escaped}%", f"%{escaped}%"))
            where = " AND ".join(conds)
            params.append(int(limit))
            rows = self.db.execute(
                f"SELECT * FROM tracks WHERE {where} "
                "ORDER BY artist ASC, title ASC, id ASC LIMIT ?",
                params,
            ).fetchall()
            return [TrackRecord.from_row(r) for r in rows]
        except Exception as e:
            log.warning("Track-Suche fehlgeschlagen für %r: %s", query, e)
            return []

    def needs_audit(self, path: str | Path, mtime: float, size: int) -> bool:
        """Gibt True zurück, wenn die Datei noch nicht erfasst ist oder sich geändert hat.

        Ermöglicht schnelles, inkrementelles Auditieren großer Sammlungen,
        ohne unveränderte Audio-Dateien erneut per Spektrumanalyse zu dekodieren.
        """
        p_str = _normalize_path(path)
        row = self.db.execute(
            "SELECT mtime, size FROM tracks WHERE path = ?", (p_str,)
        ).fetchone()
        if row is None:
            return True
        # Toleranz von 10ms für mtime wegen möglicher Rundungen im Dateisystem
        return abs(row["mtime"] - mtime) > 0.01 or row["size"] != size

    def find_by_fingerprint(self, fingerprint: str) -> list[TrackRecord]:
        """Findet Tracks mit identischem Audio-Fingerprint (Duplikaterkennung)."""
        rows = self.db.execute(
            "SELECT * FROM tracks WHERE fingerprint = ? ORDER BY id ASC",
            (fingerprint,),
        ).fetchall()
        return [TrackRecord.from_row(r) for r in rows]

    def set_fingerprint(self, track_id: int, fingerprint: str, duration: float) -> bool:
        """Setzt nur fingerprint, fingerprint_duration (und updated_at). mtime/size bleiben unverändert,
        damit der Audit-Cache gültig bleibt. True, wenn die ID existiert, sonst False.
        """
        cur = self.db.execute(
            """
            UPDATE tracks
            SET fingerprint = ?,
                fingerprint_duration = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (fingerprint, duration, track_id),
        )
        self.db.commit()
        return cur.rowcount > 0

    def tracks_missing_fingerprint(self) -> list[TrackRecord]:
        """Tracks mit fingerprint IS NULL, sortiert nach id."""
        rows = self.db.execute(
            "SELECT * FROM tracks WHERE fingerprint IS NULL ORDER BY id ASC"
        ).fetchall()
        return [TrackRecord.from_row(r) for r in rows]

    def fingerprint_candidates(self, duration: float, tolerance_s: float) -> list[TrackRecord]:
        """Tracks mit Fingerprint (nicht NULL, nicht leer) und
        duration - tolerance_s <= fingerprint_duration <= duration + tolerance_s (Grenzen eingeschlossen),
        sortiert nach id. Filter in SQL, nicht in Python (später gegen die ganze Sammlung).
        """
        min_dur = duration - tolerance_s
        max_dur = duration + tolerance_s
        rows = self.db.execute(
            """
            SELECT * FROM tracks
            WHERE fingerprint IS NOT NULL
              AND fingerprint != ''
              AND fingerprint_duration IS NOT NULL
              AND fingerprint_duration >= ?
              AND fingerprint_duration <= ?
            ORDER BY id ASC
            """,
            (min_dur, max_dur),
        ).fetchall()
        return [TrackRecord.from_row(r) for r in rows]

    def tracks_with_fingerprint(self) -> list[TrackRecord]:
        """Tracks mit Fingerprint (nicht NULL, nicht leer) und fingerprint_duration,
        sortiert nach fingerprint_duration, dann id.
        """
        rows = self.db.execute(
            """
            SELECT * FROM tracks
            WHERE fingerprint IS NOT NULL
              AND fingerprint != ''
              AND fingerprint_duration IS NOT NULL
            ORDER BY fingerprint_duration ASC, id ASC
            """
        ).fetchall()
        return [TrackRecord.from_row(r) for r in rows]

    def set_feedback(self, track_id_or_path: int | str | Path, feedback: str) -> bool:
        """Speichert DJ-Feedback (z.B. 'like', 'dislike', 'later') für einen Track."""
        if isinstance(track_id_or_path, int):
            cur = self.db.execute(
                "UPDATE tracks SET feedback = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (feedback, track_id_or_path),
            )
        else:
            p_str = _normalize_path(track_id_or_path)
            cur = self.db.execute(
                "UPDATE tracks SET feedback = ?, updated_at = CURRENT_TIMESTAMP WHERE path = ?",
                (feedback, p_str),
            )
        self.db.commit()
        return cur.rowcount > 0

    def set_sc_feedback(self, sc_id: int, value: str, url: str | None = None) -> None:
        """Upsert je SoundCloud-ID, letzte Wahl gewinnt, updated_at = jetzt.
        url=None überschreibt eine vorhandene url nicht. value nicht in FEEDBACK_VALUES -> ValueError."""
        if value not in FEEDBACK_VALUES:
            raise ValueError(f"Ungültiger Feedback-Wert: {value!r}. Erlaubt: {FEEDBACK_VALUES}")
        self.db.execute(
            """
            INSERT INTO sc_feedback (sc_id, url, value, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(sc_id) DO UPDATE SET
                value = excluded.value,
                url = COALESCE(excluded.url, sc_feedback.url),
                updated_at = CURRENT_TIMESTAMP
            """,
            (sc_id, url, value),
        )
        self.db.commit()

    def get_sc_feedback(self, sc_id: int) -> str | None:
        row = self.db.execute("SELECT value FROM sc_feedback WHERE sc_id = ?", (sc_id,)).fetchone()
        return row["value"] if row else None

    def record_exploration_use(self, tag: str, success: bool, now: datetime | None = None) -> None:
        ts = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")
        self.db.execute(
            """
            INSERT INTO exploration_tag_stats (tag, uses, successes, last_used_at, last_success_at)
            VALUES (?, 1, ?, ?, ?)
            ON CONFLICT(tag) DO UPDATE SET
                uses = uses + 1,
                successes = successes + excluded.successes,
                last_used_at = excluded.last_used_at,
                last_success_at = COALESCE(excluded.last_success_at, last_success_at)
            """,
            (tag, int(bool(success)), ts, ts if success else None),
        )
        self.db.commit()

    def top_exploration_tags(self, days: int = 30, min_successes: int = 2,
                             now: datetime | None = None) -> list[dict]:
        cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        rows = self.db.execute(
            "SELECT tag, uses, successes, last_success_at FROM exploration_tag_stats "
            "WHERE successes >= ? AND last_success_at >= ? ORDER BY successes DESC, tag",
            (min_successes, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]

    def upsert_store_item(
        self,
        sc_id: int,
        title: str,
        artist: str,
        purchase_url: str,
        purchase_title: str | None = None,
    ) -> None:
        """INSERT OR REPLACE mit UPDATE von last_seen."""
        self.db.execute(
            """
            INSERT INTO store_items (sc_id, title, artist, purchase_url, purchase_title, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(sc_id) DO UPDATE SET
                title = excluded.title,
                artist = excluded.artist,
                purchase_url = excluded.purchase_url,
                purchase_title = COALESCE(excluded.purchase_title, store_items.purchase_title),
                last_seen = CURRENT_TIMESTAMP
            """,
            (sc_id, title, artist, purchase_url, purchase_title),
        )
        self.db.commit()

    def get_store_items(
        self, only_liked: bool = False, since_days: int | None = None
    ) -> list[dict[str, Any]]:
        """Gibt alle store_items zurück.
        only_liked=True: JOIN mit sc_feedback WHERE value='like'.
        since_days: filtert nach last_seen >= NOW - since_days Tage.
        """
        conds: list[str] = []
        params: list[Any] = []
        join_clause = ""
        if only_liked:
            join_clause = "JOIN sc_feedback ON sc_feedback.sc_id = store_items.sc_id"
            conds.append("sc_feedback.value = 'like'")
        if since_days is not None:
            conds.append("datetime(store_items.last_seen) >= datetime('now', '-' || ? || ' days')")
            params.append(int(since_days))

        where_clause = f"WHERE {' AND '.join(conds)}" if conds else ""
        query = f"""
            SELECT store_items.* FROM store_items
            {join_clause}
            {where_clause}
            ORDER BY store_items.last_seen DESC, store_items.id DESC
        """
        rows = self.db.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_liked_sc_ids(self) -> list[int]:
        """Gibt alle sc_ids zurück, die in sc_feedback mit value='like' stehen."""
        rows = self.db.execute(
            "SELECT sc_id FROM sc_feedback WHERE value = 'like' ORDER BY updated_at DESC"
        ).fetchall()
        return [row[0] for row in rows]

    # ------------------------------------------------------------ Artist-Reputation

    def get_artist_reputations(self) -> dict[str, ArtistReputation]:
        """Aggregiert Reputation pro Künstler über tracks und store_items+sc_feedback.

        Key ist der normalisierte Künstlername (LOWER(TRIM(artist))). Künstler ohne
        Namen werden ignoriert. Als Download zählt ein Track in inbox/archive ohne
        explizites Feedback (like/dislike/later werden separat gezählt).
        """
        acc: dict[str, dict[str, int]] = {}

        track_rows = self.db.execute(
            """
            SELECT LOWER(TRIM(artist)) AS artist,
                   SUM(CASE WHEN feedback = 'like' THEN 1 ELSE 0 END) AS likes,
                   SUM(CASE WHEN feedback = 'dislike' THEN 1 ELSE 0 END) AS dislikes,
                   SUM(CASE WHEN feedback IS NULL AND status IN ('archive', 'inbox')
                            THEN 1 ELSE 0 END) AS downloads
            FROM tracks
            WHERE artist IS NOT NULL AND TRIM(artist) != ''
            GROUP BY LOWER(TRIM(artist))
            """
        ).fetchall()
        for row in track_rows:
            acc[row["artist"]] = {
                "downloads": row["downloads"] or 0,
                "likes": row["likes"] or 0,
                "dislikes": row["dislikes"] or 0,
            }

        store_rows = self.db.execute(
            """
            SELECT LOWER(TRIM(store_items.artist)) AS artist, sc_feedback.value AS value
            FROM store_items
            JOIN sc_feedback ON sc_feedback.sc_id = store_items.sc_id
            WHERE store_items.artist IS NOT NULL AND TRIM(store_items.artist) != ''
            """
        ).fetchall()
        for row in store_rows:
            entry = acc.setdefault(row["artist"], {"downloads": 0, "likes": 0, "dislikes": 0})
            if row["value"] == "like":
                entry["likes"] += 1
            elif row["value"] == "dislike":
                entry["dislikes"] += 1

        return {
            key: ArtistReputation(
                artist=key,
                downloads=vals["downloads"],
                likes=vals["likes"],
                dislikes=vals["dislikes"],
            )
            for key, vals in acc.items()
        }

    def get_artist_reputation(self, artist: str) -> ArtistReputation:
        """Reputation für einen einzelnen Künstler; leere Reputation, wenn unbekannt."""
        key = str(artist).strip().lower() if artist is not None else ""
        return self.get_artist_reputations().get(key, ArtistReputation(artist=key))

    # ------------------------------------------------------------ Engagement-Snapshots

    def record_track_snapshot(
        self,
        sc_id: int,
        artist: str,
        title: str | None = None,
        plays: int = 0,
        likes: int = 0,
        reposts: int = 0,
        comments: int = 0,
        recorded_at: str | None = None,
    ) -> None:
        """Speichert einen historischen Engagement-Snapshot für einen SoundCloud-Track."""
        if recorded_at is None:
            self.db.execute(
                """
                INSERT INTO track_snapshots (sc_id, artist, title, plays, likes, reposts, comments)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (sc_id, artist, title, plays, likes, reposts, comments),
            )
        else:
            self.db.execute(
                """
                INSERT INTO track_snapshots
                    (sc_id, artist, title, plays, likes, reposts, comments, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sc_id, artist, title, plays, likes, reposts, comments, recorded_at),
            )
        self.db.commit()

    def record_track_snapshots(
        self,
        tracks: Sequence[Any],
        recorded_at: str | None = None,
    ) -> int:
        """Speichert Snapshots für eine Liste von Track-Objekten.

        Liefert die Anzahl der gespeicherten Datensätze. Ignoriert Tracks ohne sc_id / id.
        """
        stored = 0
        for t in tracks:
            sc_id = getattr(t, "sc_id", None)
            if sc_id is None:
                sc_id = getattr(t, "id", None)
            if sc_id is None:
                continue
            artist = getattr(t, "artist", None) or ""
            title = getattr(t, "title", None)
            self.record_track_snapshot(
                sc_id=int(sc_id),
                artist=str(artist),
                title=title,
                plays=int(getattr(t, "plays", 0) or 0),
                likes=int(getattr(t, "likes", 0) or 0),
                reposts=int(getattr(t, "reposts", 0) or 0),
                comments=int(getattr(t, "comments", 0) or 0),
                recorded_at=recorded_at,
            )
            stored += 1
        return stored

    def count_tracks(
        self,
        status: TrackStatus | str | None = None,
        quality_status: QualityStatus | str | None = None,
    ) -> int:
        """Zählt Tracks mit optionaler Filterung nach Status oder Qualitätsurteil."""
        conds: list[str] = []
        params: list[Any] = []
        if status:
            val = status.value if isinstance(status, Enum) else status
            conds.append("status = ?")
            params.append(val)
        if quality_status:
            val = quality_status.value if isinstance(quality_status, Enum) else quality_status
            conds.append("quality_status = ?")
            params.append(val)

        where = f"WHERE {' AND '.join(conds)}" if conds else ""
        row = self.db.execute(f"SELECT COUNT(*) FROM tracks {where}", params).fetchone()
        return row[0] if row else 0

    def list_tracks(
        self,
        status: TrackStatus | str | None = None,
        quality_status: QualityStatus | str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[TrackRecord]:
        """Listet Tracks sortiert nach Aktualisierung."""
        conds: list[str] = []
        params: list[Any] = []
        if status:
            val = status.value if isinstance(status, Enum) else status
            conds.append("status = ?")
            params.append(val)
        if quality_status:
            val = quality_status.value if isinstance(quality_status, Enum) else quality_status
            conds.append("quality_status = ?")
            params.append(val)

        where = f"WHERE {' AND '.join(conds)}" if conds else ""
        limit_clause = f"LIMIT {int(limit)} OFFSET {int(offset)}" if limit is not None else ""
        query = f"SELECT * FROM tracks {where} ORDER BY id ASC {limit_clause}"
        rows = self.db.execute(query, params).fetchall()
        return [TrackRecord.from_row(r) for r in rows]

    # ------------------------------------------------------------ Job-Operationen

    def enqueue_job(
        self,
        track_id: int,
        job_type: JobType | str,
        payload: dict[str, Any] | None = None,
    ) -> JobRecord:
        """Reiht eine asynchrone Hintergrundaufgabe in die Queue ein."""
        j_type = job_type.value if isinstance(job_type, Enum) else str(job_type)
        p_json = json.dumps(payload, ensure_ascii=False) if payload else None
        cur = self.db.execute(
            """
            INSERT INTO jobs (track_id, job_type, payload, status)
            VALUES (?, ?, ?, 'pending')
            RETURNING *;
            """,
            (track_id, j_type, p_json),
        )
        row = cur.fetchone()
        self.db.commit()
        return JobRecord.from_row(row)

    def get_job_by_id(self, job_id: int) -> JobRecord | None:
        row = self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return JobRecord.from_row(row) if row else None

    def get_pending_jobs(
        self, job_type: JobType | str | None = None, limit: int = 10
    ) -> list[JobRecord]:
        """Liest anstehende Jobs (read-only, für Monitoring/Übersichten)."""
        conds = ["status = 'pending'"]
        params: list[Any] = []
        if job_type:
            val = job_type.value if isinstance(job_type, Enum) else str(job_type)
            conds.append("job_type = ?")
            params.append(val)

        query = f"""
            SELECT * FROM jobs
            WHERE {' AND '.join(conds)}
            ORDER BY id ASC
            LIMIT ?;
        """
        params.append(limit)
        rows = self.db.execute(query, params).fetchall()
        return [JobRecord.from_row(r) for r in rows]

    def claim_next_job(self, job_type: JobType | str | None = None) -> JobRecord | None:
        """Übernimmt atomar den nächsten Job und setzt ihn auf 'running'.

        Verhindert Concurrency-Kollisionen bei mehreren Workern (z.B. Server + Workstation).
        """
        jt = job_type.value if isinstance(job_type, Enum) else job_type
        row = self.db.execute(
            """
            UPDATE jobs
            SET status = 'running', updated_at = CURRENT_TIMESTAMP
            WHERE id = (
                SELECT id FROM jobs
                WHERE status = 'pending' AND (?1 IS NULL OR job_type = ?1)
                ORDER BY id LIMIT 1
            ) AND status = 'pending'
            RETURNING *;
            """,
            (jt,),
        ).fetchone()
        self.db.commit()
        return JobRecord.from_row(row) if row else None

    def touch_job(self, job_id: int) -> bool:
        """Lebenszeichen eines Workers: setzt updated_at auf jetzt, aber NUR wenn der Job
        'running' ist. True, wenn genau ein Job aktualisiert wurde, sonst False.
        """
        cur = self.db.execute(
            """
            UPDATE jobs
            SET updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'running';
            """,
            (job_id,),
        )
        self.db.commit()
        return cur.rowcount == 1

    def requeue_stale_jobs(
        self, timeout_minutes: int | None = None, job_type: JobType | str | None = None
    ) -> int:
        """Setzt 'running'-Jobs, deren updated_at älter als das Timeout ist, auf 'pending'.

        timeout_minutes=None: je Job-Typ STALE_TIMEOUT_MINUTES (Fallback 30).
        timeout_minutes=int: dieser Wert für alle Typen (bisheriges Verhalten).
        job_type wie bisher als Filter. Rückgabe: Anzahl zurückgesetzter Jobs.
        """
        jt = job_type.value if isinstance(job_type, Enum) else (str(job_type) if job_type else None)
        if timeout_minutes is not None:
            cur = self.db.execute(
                """
                UPDATE jobs
                SET status = 'pending', updated_at = CURRENT_TIMESTAMP
                WHERE status = 'running'
                  AND (?1 IS NULL OR job_type = ?1)
                  AND datetime(updated_at) < datetime('now', '-' || ?2 || ' minutes');
                """,
                (jt, int(timeout_minutes)),
            )
        else:
            when_clauses = " ".join(
                f"WHEN '{k}' THEN {int(v)}" for k, v in STALE_TIMEOUT_MINUTES.items()
            )
            case_sql = f"CASE job_type {when_clauses} ELSE 30 END"
            cur = self.db.execute(
                f"""
                UPDATE jobs
                SET status = 'pending', updated_at = CURRENT_TIMESTAMP
                WHERE status = 'running'
                  AND (?1 IS NULL OR job_type = ?1)
                  AND datetime(updated_at) < datetime('now', '-' || ({case_sql}) || ' minutes');
                """,
                (jt,),
            )
        self.db.commit()
        return cur.rowcount

    def update_job(
        self,
        job_id: int,
        status: JobStatus | str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Aktualisiert den Status und das Ergebnis eines Hintergrundjobs.

        Vorhandene Ergebnisse oder Fehlermeldungen werden nicht überschrieben,
        wenn die Parameter None sind.
        """
        s_val = status.value if isinstance(status, Enum) else str(status)
        r_json = json.dumps(result, ensure_ascii=False) if result is not None else None
        self.db.execute(
            """
            UPDATE jobs
            SET status = ?,
                result = CASE WHEN ? IS NOT NULL THEN ? ELSE result END,
                error = CASE WHEN ? IS NOT NULL THEN ? ELSE error END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """,
            (s_val, r_json, r_json, error, error, job_id),
        )
        self.db.commit()
