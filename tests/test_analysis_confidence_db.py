"""Worker-Tests für B4 Teil 1: DB-Migration, Set-Builder-Filter und Altbestand."""
import sqlite3
from pathlib import Path

import pytest

from sc_digger.db import (
    MIGRATIONS,
    TrackDB,
    TrackRecord,
    check_migrations,
)
from sc_digger.dj_export import RELIABLE_BPM_MIN_CONFIDENCE, RELIABLE_KEY_MIN_MARGIN
from sc_digger.web.database import load_tracks_for_set_builder


def _rec(path: str, bpm: float, camelot: str, **kw) -> TrackRecord:
    return TrackRecord(path=path, mtime=1.0, size=1, bpm=bpm, key_camelot=camelot,
                       artist="A", title=Path(path).stem, status="inbox", **kw)


def test_has_migration_16():
    """Migration 16 ist registriert und valide."""
    m16 = next(m for m in MIGRATIONS if m[0] == 16)
    assert m16[1] == "0016_tracks_analysis_confidence"
    assert all(col in m16[2]
               for col in ("bpm_confidence", "key_confidence", "key_margin"))


def test_check_migrations_ok():
    """Die gesamte Migrationsliste ist valide."""
    assert check_migrations() == []


def test_alte_db_wird_migriert_altzeilen_haben_null(tmp_path: Path):
    """Eine bestehende tracks.sqlite ohne die Spalten wird beim Öffnen migriert.

    Vorhandene Zeilen haben in den neuen Spalten NULL.
    """
    db_path = tmp_path / "old_tracks.sqlite"
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA foreign_keys=ON;")
    # Vollständiges Schema aus Migration 1 (CREATE TABLE IF NOT EXISTS wird
    # wegen vorhandener Tabelle übersprungen, daher müssen alle Spalten da sein)
    con.executescript("""
        CREATE TABLE tracks (
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
        CREATE INDEX IF NOT EXISTS idx_tracks_fingerprint ON tracks(fingerprint);
        INSERT INTO tracks (path, mtime, size, bpm, key_camelot, artist, title)
        VALUES ('/inbox/alt.flac', 1.0, 1, 150.0, '8A', 'A', 'Alt');
    """)
    con.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY,"
        " name TEXT NOT NULL,"
        " applied_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    # Alle bisherigen Migrationen als angewendet markieren, damit nur 16 läuft
    migrationen = [
        (1, "0001_initial_track_db"), (2, "0002_jobs_status_index"),
        (3, "0003_sc_id"), (4, "0004_track_snapshots"),
        (5, "0005_exploration_tag_stats"), (6, "0006_track_snapshots_tags"),
        (7, "0007_tracks_breakdown_ratio"), (8, "0008_tracks_adopted_at"),
        (9, "0009_tracks_rekordbox_metadata"), (10, "0010_gate_items"),
        (11, "0011_tracks_energy"), (12, "0012_tracks_duration"),
        (13, "0013_track_embeddings"), (14, "0014_tracks_sc_id"),
        (15, "0015_match_overrides"),
    ]
    for v, name in migrationen:
        con.execute("INSERT INTO schema_migrations (version, name) VALUES (?, ?)", (v, name))
    con.commit()
    con.close()

    with TrackDB(db_path) as db:
        row = db.db.execute("SELECT bpm_confidence, key_confidence, key_margin FROM tracks WHERE path = ?",
                            ("/inbox/alt.flac",)).fetchone()
    assert row is not None
    assert row["bpm_confidence"] is None
    assert row["key_confidence"] is None
    assert row["key_margin"] is None


def test_avoid_uncertain_laesst_altbestand_drin(tmp_path: Path):
    """avoid_uncertain=True filtert unsichere Tracks, aber Altbestand (NULL) bleibt."""
    db_path = _set_builder_db_mit_null(tmp_path)
    entries = load_tracks_for_set_builder(db_path, avoid_uncertain=True)
    names = sorted(e.path.name for e in entries)
    assert "altbestand_null_bpm.flac" in names
    assert "altbestand_null_key.flac" in names
    assert "sicher.flac" in names
    assert "bpm_unsicher.flac" not in names
    assert "key_unsicher.flac" not in names


def _set_builder_db_mit_null(tmp_path: Path) -> Path:
    """DB mit gemischten Sicherheitswerten, inkl. NULL-Spalten."""
    path = tmp_path / "tracks.sqlite"
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA journal_mode=WAL;")
    con.executescript("""
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE NOT NULL,
            mtime REAL NOT NULL,
            size INTEGER NOT NULL,
            bpm REAL,
            key_camelot TEXT,
            key_name TEXT,
            artist TEXT,
            title TEXT,
            status TEXT NOT NULL DEFAULT 'inbox',
            bpm_confidence REAL,
            key_confidence REAL,
            key_margin REAL
        );
        INSERT INTO tracks (path, mtime, size, bpm, key_camelot, artist, title,
                            bpm_confidence, key_confidence, key_margin)
        VALUES  ('/inbox/sicher.flac', 1, 1, 150, '8A', 'A', 'Sicher', 0.8, 0.7, 0.12),
                ('/inbox/bpm_unsicher.flac', 1, 1, 151, '8A', 'A', 'BPM unsicher', 0.3, 0.7, 0.12),
                ('/inbox/key_unsicher.flac', 1, 1, 152, '9A', 'A', 'Key unsicher', 0.8, 0.1, 0.01),
                ('/inbox/altbestand_null_bpm.flac', 1, 1, 153, '9A', 'A', 'Alt BPM', NULL, 0.7, 0.12),
                ('/inbox/altbestand_null_key.flac', 1, 1, 154, '9A', 'A', 'Alt Key', 0.8, 0.7, NULL);
    """)
    con.commit()
    con.close()
    return path


def test_schwellen_importierbar():
    """Konstanten sind korrekt definiert."""
    assert RELIABLE_BPM_MIN_CONFIDENCE == pytest.approx(0.5)
    assert RELIABLE_KEY_MIN_MARGIN == pytest.approx(0.05)