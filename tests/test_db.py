"""Tests für das zentrale Track-Datenbank-Modul (Phase 2.1)."""
import sqlite3
from pathlib import Path
import pytest

from sc_digger.db import (
    JobRecord,
    JobStatus,
    JobType,
    QualityStatus,
    TrackDB,
    TrackRecord,
    TrackStatus,
    _apply_migrations,
)


@pytest.fixture
def db(tmp_path: Path) -> TrackDB:
    db_file = tmp_path / "test_tracks.sqlite"
    with TrackDB(db_file) as instance:
        yield instance


def test_migrations_applied_on_init(db: TrackDB):
    """Prüft, ob die Version 1 der Migrationen automatisch eingetragen wurde."""
    rows = db.db.execute("SELECT version, name FROM schema_migrations").fetchall()
    assert len(rows) == 1
    assert rows[0]["version"] == 1
    assert rows[0]["name"] == "0001_initial_track_db"


def test_upsert_and_get_track(db: TrackDB):
    """Testet Einfügen und Auslesen eines Tracks per Pfad und ID."""
    record = TrackRecord(
        path="/music/Schranz/artist - track.wav",
        mtime=1700000000.0,
        size=50000000,
        format="wav",
        bitrate_kbps=1411.2,
        cutoff_hz=21500,
        quality_status=QualityStatus.OK,
        quality_details={"spectrum_cutoff": 21500, "is_lossless": True},
        bpm=155.0,
        bpm_source="audio",
        key_camelot="8A",
        key_name="Am",
        lufs=-6.5,
        true_peak_dbfs=0.8,
        loudness_range_lu=4.2,
        artist="DJ Test",
        title="Schranz Inferno",
        source_url="https://soundcloud.com/test/track",
        fingerprint="fp123456",
        fingerprint_duration=360.5,
        status=TrackStatus.ARCHIVE,
    )

    saved = db.upsert_track(record)
    assert saved.id is not None
    assert saved.id > 0
    assert saved.path == record.path
    assert saved.quality_status == QualityStatus.OK.value
    assert saved.quality_details == {"spectrum_cutoff": 21500, "is_lossless": True}

    # Per Pfad laden
    by_path = db.get_track_by_path(record.path)
    assert by_path is not None
    assert by_path.id == saved.id
    assert by_path.bpm == 155.0
    assert by_path.key_camelot == "8A"
    assert by_path.fingerprint == "fp123456"

    # Per ID laden
    by_id = db.get_track_by_id(saved.id)
    assert by_id is not None
    assert by_id.title == "Schranz Inferno"


def test_upsert_preserves_existing_data_and_feedback(db: TrackDB):
    """Review-Punkt 1: Upsert darf bestehende Werte nicht mit None überschreiben."""
    # 1. Track mit vollen Daten anlegen
    t1 = TrackRecord(
        path="/music/inbox/track.mp3",
        mtime=100.0,
        size=1000,
        bpm=156.0,
        key_camelot="5A",
        fingerprint="fp_abc",
        status=TrackStatus.INBOX,
        quality_status=QualityStatus.OK,
    )
    saved1 = db.upsert_track(t1)
    db.set_feedback(saved1.id, "like")

    # 2. Erneuter Upsert nur mit mtime/size (typisch nach Dateiscan)
    t2 = TrackRecord(
        path="/music/inbox/track.mp3",
        mtime=105.0,
        size=1000,
    )
    saved2 = db.upsert_track(t2)

    # Prüfen: Keine Daten verloren!
    assert saved2.id == saved1.id
    assert saved2.mtime == 105.0
    assert saved2.bpm == 156.0
    assert saved2.key_camelot == "5A"
    assert saved2.fingerprint == "fp_abc"
    assert saved2.status == TrackStatus.INBOX.value  # Status bleibt INBOX, nicht still auf ARCHIVE
    assert saved2.quality_status == QualityStatus.OK.value
    assert saved2.feedback == "like"  # Feedback bleibt erhalten


def test_migrations_transactional_rollback_on_failure(tmp_path: Path, monkeypatch):
    """Review-Punkt 2: Fehlgeschlagene Migration muss vollständig zurückrollen."""
    db_file = tmp_path / "rollback_test.sqlite"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT);")
    conn.commit()

    # Simulierte fehlerhafte Migration
    bad_migrations = [
        (2, "0002_broken", "CREATE TABLE temp_broken (id INT); INVALID SQL SYNTAX;")
    ]
    monkeypatch.setattr("sc_digger.db.MIGRATIONS", bad_migrations)

    with pytest.raises(Exception):
        _apply_migrations(conn)

    # Prüfen: Tabelle temp_broken darf nach Rollback NICHT existieren
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='temp_broken';")
    assert cur.fetchone() is None

    # Version 2 darf nicht in schema_migrations stehen
    cur = conn.execute("SELECT version FROM schema_migrations WHERE version=2;")
    assert cur.fetchone() is None
    conn.close()


def test_claim_next_job_atomic_and_requeue(db: TrackDB):
    """Review-Punkt 3: Atomares Übernehmen von Jobs verhindert Concurrency-Kollisionen."""
    t = db.upsert_track(TrackRecord(path="/music/t.wav", mtime=1.0, size=1))
    j1 = db.enqueue_job(t.id, JobType.EMBEDDING)
    j2 = db.enqueue_job(t.id, JobType.EMBEDDING)

    # Worker 1 übernimmt Job 1
    w1_job = db.claim_next_job(job_type=JobType.EMBEDDING)
    assert w1_job is not None
    assert w1_job.id == j1.id
    assert w1_job.status == JobStatus.RUNNING.value

    # Worker 2 übernimmt Job 2
    w2_job = db.claim_next_job(job_type=JobType.EMBEDDING)
    assert w2_job is not None
    assert w2_job.id == j2.id
    assert w2_job.status == JobStatus.RUNNING.value

    # Keine weiteren pending Jobs
    assert db.claim_next_job(job_type=JobType.EMBEDDING) is None

    # Stale Requeue testen: updated_at künstlich in die Vergangenheit setzen
    db.db.execute("UPDATE jobs SET updated_at = datetime('now', '-60 minutes') WHERE id = ?", (j1.id,))
    db.db.commit()

    requeued = db.requeue_stale_jobs(timeout_minutes=30)
    assert requeued == 1
    assert db.get_job_by_id(j1.id).status == JobStatus.PENDING.value


def test_update_job_preserves_result(db: TrackDB):
    """Review-Punkt 4: update_job darf vorhandenes Ergebnis nicht mit None überschreiben."""
    t = db.upsert_track(TrackRecord(path="/music/t2.wav", mtime=1.0, size=1))
    job = db.enqueue_job(t.id, JobType.EMBEDDING)

    # Erstes Update mit Ergebnis
    db.update_job(job.id, JobStatus.RUNNING, result={"progress": 50})
    j_mid = db.get_job_by_id(job.id)
    assert j_mid.result == {"progress": 50}

    # Zweites Update ohne Result-Argument
    db.update_job(job.id, JobStatus.COMPLETED)
    j_done = db.get_job_by_id(job.id)
    assert j_done.status == JobStatus.COMPLETED.value
    assert j_done.result == {"progress": 50}  # Bleibt erhalten!


def test_from_row_resilient_to_unknown_columns(db: TrackDB):
    """Review-Punkt 5: Unbekannte Spalten (z.B. nach Rollback) führen nicht zu TypeError."""
    # Tabelle mit zusätzlicher Spalte 'future_column'
    db.db.execute("ALTER TABLE tracks ADD COLUMN future_column TEXT;")
    db.db.execute("UPDATE tracks SET future_column = 'abc' WHERE id = 1;")
    db.db.commit()

    row = db.db.execute("SELECT * FROM tracks LIMIT 1;").fetchone()
    if row:
        # Darf keinen TypeError werfen
        record = TrackRecord.from_row(row)
        assert record is not None


def test_needs_audit(db: TrackDB):
    """Testet die inkrementelle Prüfung für den Audit-Lauf."""
    path = "/music/Schranz/track.wav"
    assert db.needs_audit(path, mtime=100.0, size=5000) is True

    db.upsert_track(TrackRecord(path=path, mtime=100.0, size=5000))
    assert db.needs_audit(path, mtime=100.0, size=5000) is False
    assert db.needs_audit(path, mtime=101.0, size=5000) is True
    assert db.needs_audit(path, mtime=100.0, size=5001) is True


def test_find_by_fingerprint(db: TrackDB):
    """Findet Duplikate mit demselben Audio-Fingerprint."""
    db.upsert_track(
        TrackRecord(
            path="/music/Schranz/original.wav",
            mtime=10.0,
            size=100,
            fingerprint="fp_hash_abc",
        )
    )
    db.upsert_track(
        TrackRecord(
            path="/music/inbox/downloaded_copy.mp3",
            mtime=12.0,
            size=80,
            fingerprint="fp_hash_abc",
            status=TrackStatus.INBOX,
        )
    )

    dupes = db.find_by_fingerprint("fp_hash_abc")
    assert len(dupes) == 2
    paths = {d.path for d in dupes}
    assert "/music/Schranz/original.wav" in paths
    assert "/music/inbox/downloaded_copy.mp3" in paths


def test_feedback_and_filtering(db: TrackDB):
    """Testet Feedback setzen sowie Zählen und Listen mit Filtern."""
    t1 = db.upsert_track(
        TrackRecord(
            path="/music/inbox/1.mp3",
            mtime=1.0,
            size=1,
            status=TrackStatus.INBOX,
            quality_status=QualityStatus.OK,
        )
    )
    t2 = db.upsert_track(
        TrackRecord(
            path="/music/inbox/2.mp3",
            mtime=1.0,
            size=1,
            status=TrackStatus.INBOX,
            quality_status=QualityStatus.FAKE_TRANSCODE,
        )
    )
    db.upsert_track(
        TrackRecord(
            path="/music/Schranz/3.wav",
            mtime=1.0,
            size=1,
            status=TrackStatus.ARCHIVE,
            quality_status=QualityStatus.OK,
        )
    )

    ok_id = db.set_feedback(t1.id, "like")
    assert ok_id is True
    ok_path = db.set_feedback("/music/inbox/2.mp3", "dislike")
    assert ok_path is True

    loaded_t1 = db.get_track_by_id(t1.id)
    assert loaded_t1 is not None and loaded_t1.feedback == "like"
    loaded_t2 = db.get_track_by_id(t2.id)
    assert loaded_t2 is not None and loaded_t2.feedback == "dislike"

    assert db.count_tracks() == 3
    assert db.count_tracks(status=TrackStatus.INBOX) == 2
    assert db.count_tracks(quality_status=QualityStatus.FAKE_TRANSCODE) == 1

    inbox_tracks = db.list_tracks(status=TrackStatus.INBOX)
    assert len(inbox_tracks) == 2
    fakes = db.list_tracks(quality_status=QualityStatus.FAKE_TRANSCODE)
    assert len(fakes) == 1
    assert fakes[0].id == t2.id
