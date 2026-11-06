"""Akzeptanztests für Web-Dashboard: „Klingt ähnlich“ im Track-Explorer (/tracks & GET /api/tracks/{track_id}/similar)."""
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.embeddings import pack_vector
from sc_digger.fingerprint import encode_fingerprint
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.database import query_similar_tracks

PASSWORD = "test-password"
AUTH = ("sc-digger", PASSWORD)


def _cfg(tmp_path: Path) -> tuple[Config, Path]:
    track_db = tmp_path / "tracks.sqlite"
    state_db = tmp_path / "state.sqlite"
    cfg = Config(raw={
        "state": {
            "track_db_path": str(track_db),
            "db_path": str(state_db),
            "health_db_path": str(state_db),
        },
        "download": {"target_dir": str(tmp_path / "inbox")},
        "collection": {"local_dirs": [str(tmp_path / "archive")]},
    })
    return cfg, track_db


def test_query_similar_tracks_missing_db_or_track(tmp_path: Path):
    missing = tmp_path / "missing.sqlite"
    assert query_similar_tracks(missing, 1) is None
    assert not missing.exists()

    _, track_db = _cfg(tmp_path)
    with TrackDB(track_db) as db:
        tid = db.upsert_track(TrackRecord(
            path="/inbox/a.mp3", mtime=1.0, size=100,
            artist="Artist A", title="Track A", bpm=155.0, key_camelot="8A", status="inbox",
        ))
    assert query_similar_tracks(track_db, 9999) is None
    res = query_similar_tracks(track_db, tid)
    assert res == {"track_id": tid, "method": "none", "items": []}


def test_query_similar_tracks_embedding_and_fingerprint_fallback(tmp_path: Path):
    _, track_db = _cfg(tmp_path)
    fp_base = [0x11111111] * 16
    fp_near = [0x11111111] * 14 + [0x11111113, 0x11111117]
    fp_far = [0xEEEEEEEE] * 16

    with TrackDB(track_db) as db:
        id1 = db.upsert_track(TrackRecord(
            path="/inbox/1.mp3", mtime=1.0, size=100,
            artist="Alpha", title="One", bpm=154.0, key_camelot="5A",
            fingerprint=encode_fingerprint(fp_base), fingerprint_duration=120.0, status="inbox",
        ))
        id2 = db.upsert_track(TrackRecord(
            path="/inbox/2.mp3", mtime=1.0, size=100,
            artist="Beta", title="Two", bpm=155.0, key_camelot="5A",
            fingerprint=encode_fingerprint(fp_near), fingerprint_duration=120.0, status="archive",
        ))
        id3 = db.upsert_track(TrackRecord(
            path="/inbox/3.mp3", mtime=1.0, size=100,
            artist="Gamma", title="Three", bpm=160.0, key_camelot="12B",
            fingerprint=encode_fingerprint(fp_far), fingerprint_duration=120.0, status="inbox",
        ))

        # Ohne Embeddings greift der Fingerprint-Fallback
        fp_res = query_similar_tracks(track_db, id1, top_n=2)
        assert fp_res is not None
        assert fp_res["method"] == "fingerprint"
        assert [item["id"] for item in fp_res["items"]] == [id2, id3]
        assert fp_res["items"][0]["similarity"] > fp_res["items"][1]["similarity"]

        # Mit Embeddings hat das Embedding-Verfahren Vorrang
        db.upsert_embedding(id1, "effnet", pack_vector([1.0, 0.0, 0.0]))
        db.upsert_embedding(id2, "effnet", pack_vector([0.2, 0.9, 0.0]))
        db.upsert_embedding(id3, "effnet", pack_vector([0.95, 0.05, 0.0]))

    emb_res = query_similar_tracks(track_db, id1, top_n=2)
    assert emb_res is not None
    assert emb_res["method"] == "embedding"
    assert [item["id"] for item in emb_res["items"]] == [id3, id2]
    assert emb_res["items"][0]["artist"] == "Gamma"


def test_api_and_tracks_ui_similar(tmp_path: Path):
    cfg, track_db = _cfg(tmp_path)
    with TrackDB(track_db) as db:
        id1 = db.upsert_track(TrackRecord(
            path="/inbox/1.mp3", mtime=1.0, size=100,
            artist="Alpha", title="One", bpm=155.0, key_camelot="8A", status="inbox",
        ))
        id2 = db.upsert_track(TrackRecord(
            path="/inbox/2.mp3", mtime=1.0, size=100,
            artist="Beta", title="Two", bpm=156.0, key_camelot="8A", status="archive",
        ))
        db.upsert_embedding(id1, "effnet", pack_vector([1.0, 0.0]))
        db.upsert_embedding(id2, "effnet", pack_vector([0.9, 0.1]))

    client = TestClient(create_app(cfg, password=PASSWORD))
    assert client.get(f"/api/tracks/{id1}/similar").status_code == 401

    r_missing = client.get("/api/tracks/9999/similar", auth=AUTH)
    assert r_missing.status_code == 404

    r_ok = client.get(f"/api/tracks/{id1}/similar?top_n=3", auth=AUTH)
    assert r_ok.status_code == 200
    body = r_ok.json()
    assert body["track_id"] == id1
    assert body["method"] == "embedding"
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == id2

    page = client.get("/tracks", auth=AUTH)
    assert page.status_code == 200
    assert "data-similar-btn" in page.text
    assert 'id="similar-panel"' in page.text
    assert "showSimilarTracks" in page.text