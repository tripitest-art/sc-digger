"""Eigene Tests für die klangliche Ähnlichkeitssuche im Track-Explorer (Issue #493).

Prüft:
- Vorrang von Audio-Embeddings vor Fingerprints
- Fallback auf Chromaprint-Fingerprints bei fehlenden Embeddings
- Rückgabe von 'none' bei Tracks ohne Audio-Merkmale
- Robuste Fehlerbehandlung (fehlende DB-Datei, unbekannte Track-ID)
- API-Endpunkt GET /api/tracks/{track_id}/similar (Auth, 404, top_n-Validierung)
- UI-Integration in /tracks (Button, Panel, JavaScript-Funktion)
"""
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.embeddings import pack_vector
from sc_digger.fingerprint import encode_fingerprint
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.database import query_similar_tracks

PASSWORD = "test-similar-password"
AUTH = ("sc-digger", PASSWORD)


def _make_config(tmp_path: Path) -> tuple[Config, Path]:
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


def test_similar_embedding_priority(tmp_path: Path):
    """Embeddings haben Vorrang vor Fingerprints; Items sind nach Ähnlichkeit sortiert."""
    _, track_db = _make_config(tmp_path)
    fp_a = [0x11111111] * 16
    fp_b = [0x22222222] * 16

    with TrackDB(track_db) as db:
        t1 = db.upsert_track(TrackRecord(
            path="/inbox/t1.mp3", mtime=1.0, size=100,
            artist="Artist 1", title="Track 1", bpm=150.0, key_camelot="8A",
            fingerprint=encode_fingerprint(fp_a), fingerprint_duration=120.0,
        ))
        t2 = db.upsert_track(TrackRecord(
            path="/inbox/t2.mp3", mtime=1.0, size=100,
            artist="Artist 2", title="Track 2", bpm=152.0, key_camelot="8A",
            fingerprint=encode_fingerprint(fp_b), fingerprint_duration=120.0,
        ))
        t3 = db.upsert_track(TrackRecord(
            path="/inbox/t3.mp3", mtime=1.0, size=100,
            artist="Artist 3", title="Track 3", bpm=154.0, key_camelot="9A",
            fingerprint=encode_fingerprint(fp_b), fingerprint_duration=120.0,
        ))

        # Beide Tracks haben Embeddings; t3 ist t1 ähnlicher als t2
        db.set_embedding(t1.id, "effnet", [1.0, 0.0, 0.0])
        db.set_embedding(t2.id, "effnet", [0.1, 0.9, 0.0])
        db.set_embedding(t3.id, "effnet", [0.9, 0.1, 0.0])

    res = query_similar_tracks(track_db, t1.id, top_n=5)
    assert res is not None
    assert res["track_id"] == t1.id
    assert res["method"] == "embedding"
    assert len(res["items"]) == 2
    assert res["items"][0]["id"] == t3.id
    assert res["items"][1]["id"] == t2.id
    assert res["items"][0]["similarity"] > res["items"][1]["similarity"]
    assert res["items"][0]["artist"] == "Artist 3"
    assert res["items"][0]["key_camelot"] == "9A"


def test_similar_fingerprint_fallback(tmp_path: Path):
    """Ohne Embeddings wird auf Chromaprint zurückgegriffen."""
    _, track_db = _make_config(tmp_path)
    fp_query = [0x12345678] * 16
    fp_close = [0x12345678] * 15 + [0x12345679]
    fp_far = [0x87654321] * 16

    with TrackDB(track_db) as db:
        t1 = db.upsert_track(TrackRecord(
            path="/inbox/f1.mp3", mtime=1.0, size=100,
            artist="F1", title="T1", bpm=150.0,
            fingerprint=encode_fingerprint(fp_query), fingerprint_duration=120.0,
        ))
        t2 = db.upsert_track(TrackRecord(
            path="/inbox/f2.mp3", mtime=1.0, size=100,
            artist="F2", title="T2", bpm=152.0,
            fingerprint=encode_fingerprint(fp_close), fingerprint_duration=120.0,
        ))
        t3 = db.upsert_track(TrackRecord(
            path="/inbox/f3.mp3", mtime=1.0, size=100,
            artist="F3", title="T3", bpm=154.0,
            fingerprint=encode_fingerprint(fp_far), fingerprint_duration=120.0,
        ))

    res = query_similar_tracks(track_db, t1.id, top_n=2)
    assert res is not None
    assert res["track_id"] == t1.id
    assert res["method"] == "fingerprint"
    assert len(res["items"]) == 2
    assert res["items"][0]["id"] == t2.id
    assert res["items"][1]["id"] == t3.id
    assert res["items"][0]["similarity"] > res["items"][1]["similarity"]


def test_similar_neither_features_returns_none_method(tmp_path: Path):
    """Track ohne Embedding und ohne Fingerprint liefert method='none' und leere Items."""
    _, track_db = _make_config(tmp_path)
    with TrackDB(track_db) as db:
        t = db.upsert_track(TrackRecord(
            path="/inbox/plain.mp3", mtime=1.0, size=100,
            artist="Plain", title="No Features",
        ))

    res = query_similar_tracks(track_db, t.id)
    assert res == {"track_id": t.id, "method": "none", "items": []}


def test_similar_missing_db_or_unknown_id(tmp_path: Path):
    """Nicht existierende DB-Datei oder unbekannte ID liefert None, ohne Datei anzulegen."""
    missing = tmp_path / "does_not_exist.sqlite"
    assert query_similar_tracks(missing, 1) is None
    assert not missing.exists()

    _, track_db = _make_config(tmp_path)
    with TrackDB(track_db) as db:
        db.upsert_track(TrackRecord(path="/inbox/a.mp3", mtime=1.0, size=100))

    assert query_similar_tracks(track_db, 12345) is None


def test_api_tracks_similar_endpoint(tmp_path: Path):
    """GET /api/tracks/{track_id}/similar prüft Auth, 404 und top_n-Beschränkung."""
    cfg, track_db = _make_config(tmp_path)
    with TrackDB(track_db) as db:
        t1 = db.upsert_track(TrackRecord(
            path="/inbox/s1.mp3", mtime=1.0, size=100,
            artist="Artist S1", title="Track S1",
        ))
        t2 = db.upsert_track(TrackRecord(
            path="/inbox/s2.mp3", mtime=1.0, size=100,
            artist="Artist S2", title="Track S2",
        ))
        t3 = db.upsert_track(TrackRecord(
            path="/inbox/s3.mp3", mtime=1.0, size=100,
            artist="Artist S3", title="Track S3",
        ))
        db.set_embedding(t1.id, "effnet", [1.0, 0.0])
        db.set_embedding(t2.id, "effnet", [0.8, 0.2])
        db.set_embedding(t3.id, "effnet", [0.7, 0.3])

    client = TestClient(create_app(cfg, password=PASSWORD))

    # 401 ohne Auth
    assert client.get(f"/api/tracks/{t1.id}/similar").status_code == 401

    # 404 für unbekannte ID
    r_404 = client.get("/api/tracks/99999/similar", auth=AUTH)
    assert r_404.status_code == 404
    assert r_404.json() == {"detail": "Track nicht gefunden"}

    # 200 mit top_n=1
    r_one = client.get(f"/api/tracks/{t1.id}/similar?top_n=1", auth=AUTH)
    assert r_one.status_code == 200
    data_one = r_one.json()
    assert len(data_one["items"]) == 1
    assert data_one["items"][0]["id"] == t2.id

    # 200 mit Standard top_n=5
    r_default = client.get(f"/api/tracks/{t1.id}/similar", auth=AUTH)
    assert r_default.status_code == 200
    data_default = r_default.json()
    assert len(data_default["items"]) == 2


def test_tracks_ui_elements(tmp_path: Path):
    """Die Seite /tracks enthält Button, Panel und JS-Funktion."""
    cfg, track_db = _make_config(tmp_path)
    with TrackDB(track_db) as db:
        t = db.upsert_track(TrackRecord(
            path="/inbox/ui.mp3", mtime=1.0, size=100,
            artist="UI Artist", title="UI Track",
        ))

    client = TestClient(create_app(cfg, password=PASSWORD))
    res = client.get("/tracks", auth=AUTH)
    assert res.status_code == 200
    assert "data-similar-btn" in res.text
    assert f'data-track-id="{t.id}"' in res.text
    assert 'id="similar-panel"' in res.text
    assert "showSimilarTracks" in res.text
