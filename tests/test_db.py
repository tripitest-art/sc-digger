"""Tests für das zentrale Track-Datenbank-Modul (Phase 2.1)."""
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


def test_upsert_updates_existing_track(db: TrackDB):
    """Testet, dass ein erneuter Upsert mit demselben Pfad die Felder aktualisiert."""
    t1 = TrackRecord(
        path="/music/Schranz/track.mp3",
        mtime=100.0,
        size=1000,
        bpm=150.0,
        quality_status=QualityStatus.UNKNOWN,
    )
    saved1 = db.upsert_track(t1)
    original_id = saved1.id

    t2 = TrackRecord(
        path="/music/Schranz/track.mp3",
        mtime=105.0,
        size=1000,
        bpm=152.0,
        quality_status=QualityStatus.OK,
        key_camelot="9B",
    )
    saved2 = db.upsert_track(t2)

    assert saved2.id == original_id
    assert saved2.bpm == 152.0
    assert saved2.quality_status == QualityStatus.OK.value
    assert saved2.key_camelot == "9B"
    assert saved2.mtime == 105.0


def test_needs_audit(db: TrackDB):
    """Testet die inkrementelle Prüfung für den Audit-Lauf."""
    path = "/music/Schranz/track.wav"
    # Nicht in DB -> muss geprüft werden
    assert db.needs_audit(path, mtime=100.0, size=5000) is True

    # In DB speichern
    db.upsert_track(TrackRecord(path=path, mtime=100.0, size=5000))

    # Unverändert -> kein Re-Audit nötig
    assert db.needs_audit(path, mtime=100.0, size=5000) is False

    # Geänderte mtime -> Re-Audit nötig
    assert db.needs_audit(path, mtime=101.0, size=5000) is True

    # Geänderte Dateigröße -> Re-Audit nötig
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

    # Feedback setzen
    ok_id = db.set_feedback(t1.id, "like")
    assert ok_id is True
    ok_path = db.set_feedback("/music/inbox/2.mp3", "dislike")
    assert ok_path is True

    loaded_t1 = db.get_track_by_id(t1.id)
    assert loaded_t1 is not None and loaded_t1.feedback == "like"
    loaded_t2 = db.get_track_by_id(t2.id)
    assert loaded_t2 is not None and loaded_t2.feedback == "dislike"

    # Count mit Filtern
    assert db.count_tracks() == 3
    assert db.count_tracks(status=TrackStatus.INBOX) == 2
    assert db.count_tracks(quality_status=QualityStatus.FAKE_TRANSCODE) == 1

    # List mit Filtern
    inbox_tracks = db.list_tracks(status=TrackStatus.INBOX)
    assert len(inbox_tracks) == 2
    fakes = db.list_tracks(quality_status=QualityStatus.FAKE_TRANSCODE)
    assert len(fakes) == 1
    assert fakes[0].id == t2.id


def test_jobs_queue_and_cascade(db: TrackDB):
    """Testet Einreihen, Abfragen, Aktualisieren von Jobs und Cascade-Delete."""
    track = db.upsert_track(TrackRecord(path="/music/track.wav", mtime=1.0, size=1))

    # Job enqueuen
    job = db.enqueue_job(
        track_id=track.id,
        job_type=JobType.EMBEDDING,
        payload={"model": "clap", "duration": 30},
    )
    assert job.id is not None
    assert job.status == JobStatus.PENDING.value
    assert job.payload == {"model": "clap", "duration": 30}

    # Ausstehende Jobs abfragen
    pending = db.get_pending_jobs(job_type=JobType.EMBEDDING)
    assert len(pending) == 1
    assert pending[0].id == job.id

    # Job updaten
    db.update_job(
        job_id=job.id,
        status=JobStatus.COMPLETED,
        result={"vector_dim": 512, "embedding_id": "vec_01"},
    )
    updated = db.get_job_by_id(job.id)
    assert updated is not None
    assert updated.status == JobStatus.COMPLETED.value
    assert updated.result == {"vector_dim": 512, "embedding_id": "vec_01"}

    # Keine weiteren pending Jobs
    assert len(db.get_pending_jobs(job_type=JobType.EMBEDDING)) == 0

    # Foreign Key Cascade: Wird der Track gelöscht, wird auch der Job gelöscht
    db.db.execute("DELETE FROM tracks WHERE id = ?", (track.id,))
    db.db.commit()
    assert db.get_job_by_id(job.id) is None
