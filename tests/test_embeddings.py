"""Unit- und Integrationstests für Audio-Embeddings (Issue #351)."""
from __future__ import annotations

import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sc_digger.db import JobStatus, JobType, TrackDB, TrackRecord, _apply_migrations
from sc_digger.embeddings import (
    apply_embedding_result,
    cosine_similarity,
    enqueue_embedding_job,
    make_embedding_handler,
    pack_vector,
    unpack_vector,
)
from sc_digger.models import Config
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "test-embeddings-pwd"
AUTH = ("sc", PASSWORD)


def make_cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def client_for(tmp_path: Path) -> TestClient:
    return TestClient(create_app(make_cfg(tmp_path), password=PASSWORD))


# ================================================================= Migrationen


def test_migration_idempotent_and_creates_table(tmp_path: Path):
    """Migration 13 legt track_embeddings an und kann mehrfach sicher ausgeführt werden."""
    db_file = tmp_path / "test_migration.sqlite"
    with TrackDB(db_file) as db:
        # Schema-Prüfung
        tables = [
            row[0]
            for row in db.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "track_embeddings" in tables

        # Prüfe Version 13 in schema_migrations
        v13 = db.db.execute(
            "SELECT version, name FROM schema_migrations WHERE version = 13"
        ).fetchone()
        assert v13 is not None
        assert v13["name"] == "0013_track_embeddings"

        # Idempotenz: erneutes _apply_migrations wirft nicht
        _apply_migrations(db.db)

    # Erneutes Öffnen von TrackDB wirft ebenfalls nicht
    with TrackDB(db_file) as db2:
        assert db2.get_embedding(1) is None


# ================================================================= Fremdschlüssel


def test_on_delete_cascade_removes_embedding(tmp_path: Path):
    """Löschen eines Tracks entfernt sein Embedding per ON DELETE CASCADE."""
    with TrackDB(tmp_path / "t.sqlite") as db:
        rec = db.upsert_track(TrackRecord(path="/test/track1.mp3", mtime=1.0, size=100))
        assert rec.id is not None
        db.set_embedding(rec.id, "model-v1", [1.0, 2.0, 3.0])
        assert db.get_embedding(rec.id) is not None

        # Track aus der DB löschen
        db.db.execute("DELETE FROM tracks WHERE id = ?", (rec.id,))
        db.db.commit()

        # Embedding muss kaskadierend gelöscht worden sein
        assert db.get_embedding(rec.id) is None
        count = db.db.execute(
            "SELECT COUNT(*) FROM track_embeddings WHERE track_id = ?", (rec.id,)
        ).fetchone()[0]
        assert count == 0


# ================================================================= Web-Hook


def test_api_jobs_complete_hook_saves_embedding(tmp_path: Path):
    """api_jobs_complete speichert das Embedding bei erfolgreichem Job-Abschluss."""
    client = client_for(tmp_path)
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        rec = db.upsert_track(TrackRecord(path="/audio/banger.wav", mtime=1.0, size=2000))
        job = enqueue_embedding_job(db, rec)
        assert job is not None

    # Job claimen
    claim = client.post("/api/jobs/claim", json={"job_type": "embedding"}, auth=AUTH)
    assert claim.status_code == 200
    assert claim.json()["job"]["id"] == job.id

    # Job mit Ergebnis abschließen
    result_payload = {
        "model": "clap-music-v1",
        "vector": [0.125, -0.25, 0.5, 0.875],
    }
    complete = client.post(
        f"/api/jobs/{job.id}/complete",
        json={"status": "completed", "result": result_payload},
        auth=AUTH,
    )
    assert complete.status_code == 200
    assert complete.json() == {"ok": True}

    # Überprüfe Speicherung in der DB
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        emb = db.get_embedding(rec.id)
        assert emb is not None
        model, vec = emb
        assert model == "clap-music-v1"
        assert vec == pytest.approx([0.125, -0.25, 0.5, 0.875])


def test_api_jobs_complete_failed_saves_no_embedding(tmp_path: Path):
    """Fehlgeschlagener Job speichert kein Embedding."""
    client = client_for(tmp_path)
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        rec = db.upsert_track(TrackRecord(path="/audio/banger2.wav", mtime=1.0, size=2000))
        job = enqueue_embedding_job(db, rec)

    client.post("/api/jobs/claim", json={"job_type": "embedding"}, auth=AUTH)
    complete = client.post(
        f"/api/jobs/{job.id}/complete",
        json={"status": "failed", "error": "CUDA out of memory"},
        auth=AUTH,
    )
    assert complete.status_code == 200

    with TrackDB(tmp_path / "tracks.sqlite") as db:
        assert db.get_embedding(rec.id) is None


# ================================================================= Queue & Deduping


def test_completed_job_is_not_considered_open(tmp_path: Path):
    """Ein abgeschlossener oder fehlgeschlagener Job blockiert kein neues Einreihen."""
    with TrackDB(tmp_path / "t.sqlite") as db:
        rec = db.upsert_track(TrackRecord(path="/audio/track.mp3", mtime=1.0, size=100))
        job1 = enqueue_embedding_job(db, rec)
        assert job1 is not None

        # Solange Job pending/running ist, liefert enqueue denselben Job zurück
        job1_dup = enqueue_embedding_job(db, rec)
        assert job1_dup.id == job1.id

        # Auf 'failed' setzen (ohne Embedding)
        db.update_job(job1.id, JobStatus.FAILED, error="test error")
        job2 = enqueue_embedding_job(db, rec)
        assert job2 is not None
        assert job2.id != job1.id

        # Auf 'completed' setzen (ohne Embedding zu speichern)
        db.update_job(job2.id, JobStatus.COMPLETED)
        job3 = enqueue_embedding_job(db, rec)
        assert job3 is not None
        assert job3.id != job2.id

        # Sobald ein Embedding vorliegt, wird gar kein Job mehr eingereiht
        db.set_embedding(rec.id, "m", [1.0, 2.0])
        assert enqueue_embedding_job(db, rec) is None


# ================================================================= pack / unpack


def test_pack_unpack_edge_cases():
    """Prüft Randfälle für pack_vector und unpack_vector."""
    # Booleans dürfen nicht als float gepackt werden
    with pytest.raises(ValueError):
        pack_vector([True, 1.0])  # type: ignore[list-item]

    # Nicht-Zahlen
    with pytest.raises(ValueError):
        pack_vector(["0.5", 1.0])  # type: ignore[list-item]

    # Unendliche Werte
    with pytest.raises(ValueError):
        pack_vector([math.inf, 1.0])
    with pytest.raises(ValueError):
        pack_vector([-math.inf, 1.0])

    # Ungültige Blobs
    with pytest.raises(ValueError):
        unpack_vector(b"")
    with pytest.raises(ValueError):
        unpack_vector(b"\x00" * 3)
    with pytest.raises(ValueError):
        unpack_vector(b"\x00" * 5)
    with pytest.raises(ValueError):
        unpack_vector("kein-bytes")  # type: ignore[arg-type]


# ================================================================= cosine_similarity


def test_cosine_similarity_edge_cases():
    """Prüft Randfälle von cosine_similarity."""
    # Norm 0 Vektoren
    assert cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0
    assert cosine_similarity([0.0, 0.0, 0.0], [1.0, 2.0, 3.0]) == 0.0

    # Orthogonal
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    # Entgegengesetzt
    assert cosine_similarity([0.0, 2.0], [0.0, -5.0]) == pytest.approx(-1.0)

    # Leere Vektoren
    assert cosine_similarity([], []) == 0.0

    # Unterschiedliche Längen
    with pytest.raises(ValueError):
        cosine_similarity([1.0], [1.0, 2.0])

    # Nicht-endliche Werte
    with pytest.raises(ValueError):
        cosine_similarity([math.nan], [1.0])
    with pytest.raises(ValueError):
        cosine_similarity([1.0], [math.inf])

    # Clamping Grenzen
    assert cosine_similarity([10.0], [10.0]) <= 1.0
    assert cosine_similarity([-10.0], [10.0]) >= -1.0


# ================================================================= apply_embedding_result


def test_apply_embedding_result_edge_cases(tmp_path: Path):
    """Prüft Validierungslogik von apply_embedding_result."""
    with TrackDB(tmp_path / "t.sqlite") as db:
        rec = db.upsert_track(TrackRecord(path="/test/x.mp3", mtime=1.0, size=10))
        job = enqueue_embedding_job(db, rec)

        # Kein Dict als Ergebnis
        assert apply_embedding_result(db, job.id, "kein dict") is False  # type: ignore[arg-type]
        assert apply_embedding_result(db, job.id, None) is False  # type: ignore[arg-type]

        # Ungültiges/leeres Model
        assert apply_embedding_result(db, job.id, {"model": "   ", "vector": [1.0]}) is False
        assert apply_embedding_result(db, job.id, {"model": 123, "vector": [1.0]}) is False  # type: ignore[dict-item]

        # Ungültiger Vektor
        assert apply_embedding_result(db, job.id, {"model": "m", "vector": None}) is False  # type: ignore[dict-item]
        assert apply_embedding_result(db, job.id, {"model": "m", "vector": "nicht liste"}) is False  # type: ignore[dict-item]
        assert apply_embedding_result(db, job.id, {"model": "m", "vector": [math.nan]}) is False
        assert apply_embedding_result(db, job.id, {"model": "m", "vector": [False, 1.0]}) is False

        # Unbekannter Job
        assert apply_embedding_result(db, 99999, {"model": "m", "vector": [1.0]}) is False


# ================================================================= make_embedding_handler


def test_make_embedding_handler_exceptions(tmp_path: Path):
    """Handler reicht Ausnahmen transparent durch."""
    def boom_fetch(payload):
        raise FileNotFoundError("Audio nicht gefunden")

    handler1 = make_embedding_handler(lambda p: [1.0], boom_fetch, "m")
    with pytest.raises(FileNotFoundError):
        handler1({"track_id": 1})

    def boom_embed(path):
        raise ValueError("Model error")

    handler2 = make_embedding_handler(boom_embed, lambda p: tmp_path / "dummy.mp3", "m")
    with pytest.raises(ValueError):
        handler2({"track_id": 1})
