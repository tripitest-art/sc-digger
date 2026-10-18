import math
from pathlib import Path

import pytest

from sc_digger.db import JobType, TrackDB, TrackRecord
from sc_digger.embeddings import (
    apply_embedding_result,
    cosine_similarity,
    enqueue_embedding_job,
    make_embedding_handler,
    pack_vector,
    unpack_vector,
)
from sc_digger.worker import execute_job


def _track(db: TrackDB, name: str = "a") -> TrackRecord:
    return db.upsert_track(TrackRecord(path=f"/m/{name}.mp3", mtime=1.0, size=1, status="inbox"))


def test_pack_roundtrip_and_errors():
    v = [0.5, -1.25, 3.0]
    assert unpack_vector(pack_vector(v)) == pytest.approx(v)
    with pytest.raises(ValueError):
        pack_vector([])
    with pytest.raises(ValueError):
        pack_vector([1.0, math.nan])
    with pytest.raises(ValueError):
        unpack_vector(b"abc")


def test_cosine_similarity():
    assert cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0)
    assert cosine_similarity([0, 0], [1, 1]) == 0.0
    with pytest.raises(ValueError):
        cosine_similarity([1, 0], [1, 0, 0])


def test_db_embedding_roundtrip_and_replace(tmp_path: Path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        rec = _track(db)
        assert db.get_embedding(rec.id) is None
        db.set_embedding(rec.id, "fake-1", [1.0, 2.0])
        model, vec = db.get_embedding(rec.id)
        assert model == "fake-1" and vec == pytest.approx([1.0, 2.0])
        db.set_embedding(rec.id, "fake-2", [3.0, 4.0])
        assert db.get_embedding(rec.id)[0] == "fake-2"
        other = _track(db, "b")
        assert [p[0].id for p in db.embedded_tracks()] == [rec.id]
        db.set_embedding(other.id, "fake-2", [0.0, 1.0])
        assert [p[0].id for p in db.embedded_tracks()] == [rec.id, other.id]


def test_enqueue_embedding_job(tmp_path: Path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        rec = _track(db)
        job = enqueue_embedding_job(db, rec)
        assert job is not None and job.job_type == JobType.EMBEDDING
        assert job.payload["track_id"] == rec.id and job.payload["path"] == rec.path
        again = enqueue_embedding_job(db, rec)
        assert again is not None and again.id == job.id
        db.set_embedding(rec.id, "m", [1.0])
        assert enqueue_embedding_job(db, rec) is None
        assert enqueue_embedding_job(db, TrackRecord(path="/x.mp3", mtime=1.0, size=1)) is None


def test_apply_embedding_result(tmp_path: Path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        rec = _track(db)
        job = enqueue_embedding_job(db, rec)
        assert apply_embedding_result(db, job.id, {"model": "m", "vector": [0.1, 0.2]}) is True
        assert db.get_embedding(rec.id)[0] == "m"
        assert apply_embedding_result(db, 9999, {"model": "m", "vector": [1.0]}) is False
        assert apply_embedding_result(db, job.id, {"model": "", "vector": [1.0]}) is False
        assert apply_embedding_result(db, job.id, {"model": "m", "vector": []}) is False
        assert apply_embedding_result(db, job.id, {"model": "m", "vector": ["x"]}) is False


def test_handler_with_fakes(tmp_path: Path):
    seen = []

    def fetch(payload):
        seen.append(payload["track_id"])
        return tmp_path / "x.mp3"

    handler = make_embedding_handler(lambda p: (1, 2, 3), fetch, "fake")
    out = execute_job({"id": 1, "job_type": "embedding", "payload": {"track_id": 7}},
                      {"embedding": handler})
    assert out == {"status": "completed", "result": {"model": "fake", "vector": [1.0, 2.0, 3.0]}}
    assert seen == [7]

    def boom(path):
        raise RuntimeError("kaputt")

    bad = make_embedding_handler(boom, fetch, "fake")
    out = execute_job({"id": 2, "job_type": "embedding", "payload": {"track_id": 8}},
                      {"embedding": bad})
    assert out["status"] == "failed" and "kaputt" in out["error"]
