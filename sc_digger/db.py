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
import re
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

from .fingerprint import decode_fingerprint

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
    CUES = "cues"                  # Cue- und Strukturanalyse (GPU-Workstation)


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
    # SoundCloud-ID; bei Inbox-Dateien aus dem Präfix [<id>], bei übernommenen
    # Sammlungsdateien per Fingerprint übertragen (ROADMAP 3.12).
    sc_id: int | None = None
    fingerprint: str | None = None
    fingerprint_duration: float | None = None
    status: TrackStatus | str | None = None
    feedback: str | None = None  # 'like', 'dislike', 'later'
    adopted_at: str | None = None  # Zeitstempel der Übernahme in die Sammlung (Format: YYYY-MM-DD HH:MM:SS)
    breakdown_ratio: float | None = None
    # Rekordbox-Import (nur lesend aus dem XML-Export, siehe sc_digger.rekordbox):
    rb_rating: int | None = None      # 0..5 Sterne (berechnet aus Rekordbox Rating 0..255)
    rb_play_count: int | None = None  # Abspielzähler aus PlayCount
    rb_bpm: float | None = None       # Rekordbox AverageBpm
    rb_key: str | None = None         # Rekordbox Tonality
    rb_cues: str | None = None        # JSON-String der Cue-Liste
    energy: int | None = None         # 1..5 Energie-Level (berechnet, siehe sc_digger.style)
    duration: float | None = None     # Länge der Datei in Sekunden (ffprobe)
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
    (
        6,
        "0006_track_snapshots_tags",
        """
        ALTER TABLE track_snapshots ADD COLUMN tags TEXT;
        """,
    ),
    (
        7,
        "0007_tracks_breakdown_ratio",
        """
        ALTER TABLE tracks ADD COLUMN breakdown_ratio REAL;
        """,
    ),
    (
        8,
        "0008_tracks_adopted_at",
        """
        ALTER TABLE tracks ADD COLUMN adopted_at TEXT;
        CREATE INDEX IF NOT EXISTS idx_tracks_adopted_at ON tracks(adopted_at);
        """,
    ),
    (
        9,
        "0009_tracks_rekordbox_metadata",
        """
        ALTER TABLE tracks ADD COLUMN rb_rating INTEGER;
        ALTER TABLE tracks ADD COLUMN rb_play_count INTEGER;
        ALTER TABLE tracks ADD COLUMN rb_bpm REAL;
        ALTER TABLE tracks ADD COLUMN rb_key TEXT;
        ALTER TABLE tracks ADD COLUMN rb_cues TEXT;
        CREATE INDEX IF NOT EXISTS idx_tracks_rb_rating ON tracks(rb_rating);
        """,
    ),
    (
        10,
        "0010_gate_items",
        """
        CREATE TABLE IF NOT EXISTS gate_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sc_id INTEGER UNIQUE NOT NULL,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            gate_url TEXT NOT NULL,
            gate_type TEXT,
            first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_gate_items_last_seen ON gate_items(last_seen);
        """,
    ),
    (
        # Nummer 11 statt der im Issue genannten 10: 10 ist bereits durch 0010_gate_items belegt.
        11,
        "0011_tracks_energy",
        """
        ALTER TABLE tracks ADD COLUMN energy INTEGER;
        CREATE INDEX IF NOT EXISTS idx_tracks_energy ON tracks(energy);
        """,
    ),
    (
        12,
        "0012_tracks_duration",
        """
        ALTER TABLE tracks ADD COLUMN duration REAL;
        CREATE INDEX IF NOT EXISTS idx_tracks_duration ON tracks(duration);
        """,
    ),
    (
        13,
        "0013_track_embeddings",
        """
        CREATE TABLE IF NOT EXISTS track_embeddings (
            track_id INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
            model TEXT NOT NULL,
            dim INTEGER NOT NULL,
            vector BLOB NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_track_embeddings_model ON track_embeddings(model);
        """,
    ),
    (
        14,
        "0014_tracks_sc_id",
        """
        ALTER TABLE tracks ADD COLUMN sc_id INTEGER;
        CREATE INDEX IF NOT EXISTS idx_tracks_sc_id ON tracks(sc_id);
        """,
    ),
    (
        15,
        "0015_match_overrides",
        """
        CREATE TABLE IF NOT EXISTS match_overrides (
            sc_id INTEGER PRIMARY KEY,
            override_type TEXT NOT NULL CHECK (override_type IN ('already_in_collection', 'not_duplicate')),
            note TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """,
    ),
]

# Erlaubte Werte in ``match_overrides.override_type``: die Handkorrektur ersetzt den
# Namens-Abgleich, deshalb bleiben es genau diese zwei Richtungen.
MATCH_OVERRIDE_TYPES = ("already_in_collection", "not_duplicate")


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

    def get_track_by_filename(self, filename: str) -> TrackRecord | None:
        """Findet einen Track anhand seines Dateinamens (Pfad-Endung).

        Nützlich, wenn die Rekordbox-XML einen anderen Ordner nennt als die eigene DB
        (z. B. nach dem Sortieren): der reine Dateiname bleibt stabil. LIKE-Sonderzeichen
        im Namen werden escaped, damit ein wörtlicher "_" nicht wie ein Platzhalter wirkt.
        Bei mehreren Treffern gewinnt die kleinste id. Wirft nie (DB-Fehler -> None).
        """
        try:
            escaped = (
                str(filename).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            row = self.db.execute(
                "SELECT * FROM tracks WHERE path LIKE ? ESCAPE '\\' ORDER BY id ASC LIMIT 1",
                (f"%/{escaped}",),
            ).fetchone()
            return TrackRecord.from_row(row) if row else None
        except Exception as e:
            log.warning("Track-Suche nach Dateiname fehlgeschlagen für %r: %s", filename, e)
            return None

    def update_rekordbox_metadata(
        self,
        track_id: int,
        rb_rating: int | None = None,
        rb_play_count: int | None = None,
        rb_bpm: float | None = None,
        rb_key: str | None = None,
        rb_cues: str | None = None,
        auto_like: bool = True,
    ) -> bool:
        """Aktualisiert Rekordbox-Metadaten für einen Track.

        Falls auto_like=True und ((rb_rating is not None and rb_rating >= 4) oder
        (rb_play_count is not None and rb_play_count >= 2)):
        falls feedback IS NULL ist, wird feedback auf 'like' gesetzt.
        Bestehendes feedback ('like', 'dislike', 'later') wird nie überschrieben.
        Aktualisiert updated_at = CURRENT_TIMESTAMP.
        Gibt True zurück, wenn der Track existiert und aktualisiert wurde, sonst False.
        """
        row = self.db.execute(
            "SELECT feedback FROM tracks WHERE id = ?", (track_id,)
        ).fetchone()
        if row is None:
            return False

        set_parts = [
            "rb_rating = ?",
            "rb_play_count = ?",
            "rb_bpm = ?",
            "rb_key = ?",
            "rb_cues = ?",
            "updated_at = CURRENT_TIMESTAMP",
        ]
        params: list[Any] = [rb_rating, rb_play_count, rb_bpm, rb_key, rb_cues]

        # Auto-Like ist ein Positivsignal und darf nur ein noch leeres Feedback füllen.
        if auto_like and row["feedback"] is None and (
            (rb_rating is not None and rb_rating >= 4)
            or (rb_play_count is not None and rb_play_count >= 2)
        ):
            set_parts.append("feedback = 'like'")

        self.db.execute(
            f"UPDATE tracks SET {', '.join(set_parts)} WHERE id = ?",
            (*params, track_id),
        )
        self.db.commit()
        return True

    def set_energy(self, track_id: int, energy: int) -> bool:
        """Setzt die Energie-Stufe (1..5) für einen Track.

        Gibt True zurück, wenn der Track existiert und aktualisiert wurde, sonst False.
        """
        cur = self.db.execute(
            "UPDATE tracks SET energy = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (energy, track_id),
        )
        self.db.commit()
        return cur.rowcount > 0

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

    def fingerprinted_tracks(self) -> list[tuple[TrackRecord, list[int]]]:
        """Alle Tracks mit Fingerprint als (TrackRecord, Rohwerte), sortiert nach path, id.

        Read-only: entschlüsselt den gespeicherten Fingerprint (base64) einmal für die
        Ähnlichkeitssuche. Tracks ohne Fingerprint bleiben draußen; leere DB -> [].
        """
        rows = self.db.execute(
            """
            SELECT * FROM tracks
            WHERE fingerprint IS NOT NULL
              AND fingerprint != ''
            ORDER BY path ASC, id ASC
            """
        ).fetchall()
        return [(TrackRecord.from_row(r), decode_fingerprint(r["fingerprint"])) for r in rows]

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

    def mark_adopted(
        self,
        track_id: int,
        adopted_at: str,
        *,
        archive_track_id: int | None = None,
        feedback: str = "like",
    ) -> bool:
        """Markiert den Track mit ID track_id als übernommen (adopted_at = adopted_at).
        Ein bestehendes feedback='dislike' wird NIEMALS überschrieben.
        Falls feedback != 'dislike', wird feedback auf den übergebenen Wert (Standard 'like') gesetzt.
        Aktualisiert updated_at = CURRENT_TIMESTAMP.
        Ist archive_track_id gesetzt, wandert die SoundCloud-ID (Präfix [<id>] im Dateinamen,
        sonst sc_id von track_id) samt source_url auf die Archivzeile; eine dort bereits
        gesetzte source_url bleibt stehen. Eine unbekannte track_id ändert auch die
        Archivzeile nicht.
        Gibt True zurück, wenn der Track existiert und aktualisiert wurde, sonst False.
        """
        # feedback nur setzen, wenn der Track nicht bereits ein 'dislike' trägt:
        # Übernahme ist ein Positivsignal, darf ein explizites 👎 aber nie überstimmen.
        cur = self.db.execute(
            """
            UPDATE tracks
            SET adopted_at = ?,
                feedback = CASE
                    WHEN feedback = 'dislike' THEN feedback
                    ELSE ?
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (adopted_at, feedback, track_id),
        )
        changed = cur.rowcount > 0
        self.db.commit()
        if not changed:
            return False

        if archive_track_id is not None:
            row = self.db.execute(
                "SELECT path, sc_id, source_url FROM tracks WHERE id = ?", (track_id,)
            ).fetchone()
            if row is not None:
                # Das Präfix im Dateinamen ist die frischere Angabe; erst danach zählt
                # eine bereits gespeicherte sc_id des Records.
                m = re.match(r"^\[(\d+)\]", Path(row["path"] or "").name)
                sc_id = int(m.group(1)) if m else row["sc_id"]
                set_parts: list[str] = []
                params: list[Any] = []
                if sc_id is not None:
                    set_parts.append("sc_id = ?")
                    params.append(int(sc_id))
                if row["source_url"]:
                    # Nur eine leere URL füllen: die Sammlungszeile bleibt die Wahrheit.
                    set_parts.append("source_url = COALESCE(NULLIF(source_url, ''), ?)")
                    params.append(str(row["source_url"]))
                if set_parts:
                    set_parts.append("updated_at = CURRENT_TIMESTAMP")
                    params.append(archive_track_id)
                    self.db.execute(
                        f"UPDATE tracks SET {', '.join(set_parts)} WHERE id = ?",
                        params,
                    )
                    self.db.commit()
        return True

    def get_adopted_tracks(self) -> list[TrackRecord]:
        """Gibt alle Tracks mit adopted_at IS NOT NULL zurück, sortiert nach adopted_at DESC, id ASC."""
        rows = self.db.execute(
            "SELECT * FROM tracks WHERE adopted_at IS NOT NULL "
            "ORDER BY adopted_at DESC, id ASC"
        ).fetchall()
        return [TrackRecord.from_row(r) for r in rows]

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

    def set_match_override(self, sc_id: int, override_type: str, note: str = "") -> None:
        """Speichert eine Handkorrektur des Matchers je SoundCloud-ID, letzte Wahl gewinnt.

        ``already_in_collection`` markiert einen Falsch-Negativ als vorhanden,
        ``not_duplicate`` gibt einen Falsch-Positiv wieder frei. Unbekannter Typ ->
        ``ValueError``, dann wird nichts gespeichert.
        """
        if override_type not in MATCH_OVERRIDE_TYPES:
            raise ValueError(
                f"Ungültiger Override-Typ: {override_type!r}. Erlaubt: {MATCH_OVERRIDE_TYPES}"
            )
        self.db.execute(
            """
            INSERT INTO match_overrides (sc_id, override_type, note, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(sc_id) DO UPDATE SET
                override_type = excluded.override_type,
                note = excluded.note,
                updated_at = CURRENT_TIMESTAMP
            """,
            (int(sc_id), override_type, note or None),
        )
        self.db.commit()

    def get_match_override(self, sc_id: int) -> str | None:
        row = self.db.execute(
            "SELECT override_type FROM match_overrides WHERE sc_id = ?", (int(sc_id),)
        ).fetchone()
        return str(row["override_type"]) if row else None

    def list_match_overrides(self) -> dict[int, str]:
        """Alle Korrekturen als ``{sc_id: override_type}`` (leer, wenn keine gesetzt sind)."""
        rows = self.db.execute("SELECT sc_id, override_type FROM match_overrides").fetchall()
        return {int(r["sc_id"]): str(r["override_type"]) for r in rows if r["sc_id"] is not None}

    def remove_match_override(self, sc_id: int) -> bool:
        """Löscht die Korrektur zu ``sc_id``; ``True``, wenn ein Eintrag vorhanden war."""
        cur = self.db.execute("DELETE FROM match_overrides WHERE sc_id = ?", (int(sc_id),))
        self.db.commit()
        return cur.rowcount > 0

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

    def upsert_gate_item(
        self,
        sc_id: int,
        title: str,
        artist: str,
        gate_url: str,
        gate_type: str | None = None,
    ) -> None:
        """Fügt ein Gate-Item (Hypeddit, Droploud, etc.) in gate_items ein oder aktualisiert last_seen."""
        self.db.execute(
            """
            INSERT INTO gate_items (sc_id, title, artist, gate_url, gate_type, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(sc_id) DO UPDATE SET
                title = excluded.title,
                artist = excluded.artist,
                gate_url = excluded.gate_url,
                gate_type = COALESCE(excluded.gate_type, gate_items.gate_type),
                last_seen = CURRENT_TIMESTAMP
            """,
            (sc_id, title, artist, gate_url, gate_type),
        )
        self.db.commit()

    def get_gate_items(self) -> list[dict[str, Any]]:
        """Gibt alle gate_items sortiert nach last_seen DESC zurück."""
        rows = self.db.execute(
            "SELECT * FROM gate_items ORDER BY last_seen DESC, id DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_liked_sc_ids(self) -> list[int]:
        """Gibt alle sc_ids zurück, die in sc_feedback mit value='like' stehen."""
        rows = self.db.execute(
            "SELECT sc_id FROM sc_feedback WHERE value = 'like' ORDER BY updated_at DESC"
        ).fetchall()
        return [row[0] for row in rows]

    def get_top_collection_artists(self, min_tracks: int = 3, limit: int = 30) -> list[tuple[str, int]]:
        """Ermittelt Künstler mit mindestens min_tracks Tracks in der Sammlung (status = 'archive' oder adopted_at IS NOT NULL).
        Gibt Liste von (artist_name, track_count) sortiert nach track_count DESC, artist_name ASC zurück.
        Ignoriert leere Künstlernamen."""
        # Schreibweisen wie "O.B.I." und "o.b.i." zählen zusammen; angezeigt wird die
        # häufigste Schreibweise (bei Gleichstand die alphabetisch erste).
        rows = self.db.execute(
            """
            SELECT TRIM(artist) AS artist_name, COUNT(*) AS track_count
            FROM tracks
            WHERE artist IS NOT NULL AND TRIM(artist) != ''
              AND (status = 'archive' OR adopted_at IS NOT NULL)
            GROUP BY LOWER(TRIM(artist)), TRIM(artist)
            """
        ).fetchall()

        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            name = row["artist_name"]
            key = name.lower()
            entry = grouped.setdefault(key, {"count": 0, "spellings": Counter()})
            entry["count"] += int(row["track_count"])
            entry["spellings"][name] += int(row["track_count"])

        artists: list[tuple[str, int]] = []
        for key, entry in grouped.items():
            if entry["count"] < min_tracks:
                continue
            spellings = entry["spellings"]
            best = sorted(spellings.items(), key=lambda item: (-item[1], item[0]))[0][0]
            artists.append((best, entry["count"]))

        artists.sort(key=lambda item: (-item[1], item[0].lower()))
        return artists[:limit]

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
        tags: list[str] | None = None,
    ) -> None:
        """Speichert einen historischen Engagement-Snapshot für einen SoundCloud-Track.

        Tags werden als JSON-Liste (ensure_ascii=False) abgelegt, damit Tags mit
        Komma verlustfrei bleiben; leer oder None ergibt NULL.
        """
        tags_json = json.dumps(tags, ensure_ascii=False) if tags else None
        if recorded_at is None:
            self.db.execute(
                """
                INSERT INTO track_snapshots
                    (sc_id, artist, title, plays, likes, reposts, comments, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sc_id, artist, title, plays, likes, reposts, comments, tags_json),
            )
        else:
            self.db.execute(
                """
                INSERT INTO track_snapshots
                    (sc_id, artist, title, plays, likes, reposts, comments, recorded_at, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sc_id, artist, title, plays, likes, reposts, comments, recorded_at, tags_json),
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
                tags=list(getattr(t, "tags", None) or []),
            )
            stored += 1
        return stored

    def record_track_snapshots_daily(
        self,
        tracks: Sequence[Any],
        recorded_at: str | None = None,
    ) -> int:
        """Speichert Snapshots nur für Tracks ohne Snapshot am selben UTC-Kalendertag.

        Der Vergleich läuft über `date(recorded_at)`. `recorded_at` None bedeutet
        jetzt (UTC). Objekte ohne sc_id / id werden ignoriert. Liefert die Zahl neu
        gespeicherter Snapshots.
        """
        if recorded_at is None:
            recorded_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        day = recorded_at[:10]
        stored = 0
        for t in tracks:
            sc_id = getattr(t, "sc_id", None)
            if sc_id is None:
                sc_id = getattr(t, "id", None)
            if sc_id is None:
                continue
            exists = self.db.execute(
                "SELECT 1 FROM track_snapshots "
                "WHERE sc_id = ? AND date(recorded_at) = ? LIMIT 1",
                (int(sc_id), day),
            ).fetchone()
            if exists is not None:
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
                tags=list(getattr(t, "tags", None) or []),
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

    def list_collection_tracks(self) -> list[TrackRecord]:
        """Alle Tracks mit status in ('archive', 'adopted'), nach id aufsteigend."""
        rows = self.db.execute(
            "SELECT * FROM tracks WHERE status IN ('archive', 'adopted') ORDER BY id ASC"
        ).fetchall()
        return [TrackRecord.from_row(r) for r in rows]

    def _row_to_track(self, row: sqlite3.Row) -> TrackRecord:
        return TrackRecord.from_row(row)

    def set_embedding(self, track_id: int, model: str, vector: Sequence[float]) -> None:
        """Speichert oder aktualisiert ein Audio-Embedding für einen Track."""
        from .embeddings import pack_vector

        dim = len(vector)
        blob = pack_vector(vector)
        self.db.execute(
            """
            INSERT INTO track_embeddings (track_id, model, dim, vector, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(track_id) DO UPDATE SET
                model = excluded.model,
                dim = excluded.dim,
                vector = excluded.vector,
                updated_at = CURRENT_TIMESTAMP;
            """,
            (track_id, model, dim, blob),
        )
        self.db.commit()

    def get_embedding(self, track_id: int) -> tuple[str, list[float]] | None:
        """Liest das gespeicherte Embedding (Modellname, Vektor) eines Tracks."""
        row = self.db.execute(
            "SELECT model, vector FROM track_embeddings WHERE track_id = ?",
            (track_id,),
        ).fetchone()
        if row is None:
            return None
        from .embeddings import unpack_vector

        return (row["model"], unpack_vector(row["vector"]))

    def embedded_tracks(self) -> list[tuple[TrackRecord, list[float]]]:
        """Liest alle Tracks mit gespeichertem Embedding aufsteigend nach Track-ID."""
        from .embeddings import unpack_vector

        rows = self.db.execute(
            """
            SELECT t.*, e.vector
            FROM tracks t
            JOIN track_embeddings e ON t.id = e.track_id
            ORDER BY t.id ASC
            """
        ).fetchall()
        return [(self._row_to_track(r), unpack_vector(r["vector"])) for r in rows]

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

    def find_active_job(self, job_type: JobType | str, track_id: int) -> JobRecord | None:
        """Sucht nach einem anstehenden oder laufenden Job für diesen Track."""
        jt = job_type.value if isinstance(job_type, Enum) else str(job_type)
        row = self.db.execute(
            """
            SELECT * FROM jobs
            WHERE track_id = ? AND job_type = ? AND status IN ('pending', 'running')
            ORDER BY id ASC
            LIMIT 1;
            """,
            (track_id, jt),
        ).fetchone()
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


def enqueue_cue_job(db: TrackDB, track: TrackRecord) -> JobRecord | None:
    """Reiht einen Cue-Analyse-Job für den Track ein (``JobType.CUES``).

    Payload: ``{"track_id", "path", "format"}``. Ohne ``track.id`` gibt es nichts
    einzureihen (der Track liegt dann noch nicht in der DB) – Rückgabe ``None``.
    DB-Fehler werden nicht geschluckt: der Aufrufer entscheidet, ob sie den Lauf
    betreffen (Goldene Regel 7).
    """
    if track.id is None:
        return None
    payload = {"track_id": track.id, "path": track.path, "format": track.format}
    return db.enqueue_job(track.id, JobType.CUES, payload=payload)
