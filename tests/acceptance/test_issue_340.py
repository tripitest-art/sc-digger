"""Akzeptanztests: Harmonischer Set-Builder im Web-Dashboard (Issue #339)."""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "test-set-builder"


def make_test_cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def test_api_set_builder_creates_harmonic_chain(tmp_path: Path):
    cfg = make_test_cfg(tmp_path)
    db_path = cfg["state"]["track_db_path"]

    with TrackDB(db_path) as db:
        # Drei Tracks: 8A (155), 8A (155.5), 9A (156) -> perfekte Kette
        db.upsert_track(TrackRecord(path="audio/t1.wav", mtime=1.0, size=10, status="archive", artist="A1", title="T1", bpm=155.0, key_camelot="8A"))
        db.upsert_track(TrackRecord(path="audio/t2.wav", mtime=1.0, size=10, status="archive", artist="A2", title="T2", bpm=155.5, key_camelot="8A"))
        db.upsert_track(TrackRecord(path="audio/t3.wav", mtime=1.0, size=10, status="archive", artist="A3", title="T3", bpm=156.0, key_camelot="9A"))

    client = TestClient(create_app(cfg, password=PASSWORD))
    auth = ("sc", PASSWORD)

    # 1. POST /api/set-builder
    resp = client.post("/api/set-builder", json={"bpm_min": 150.0, "bpm_max": 160.0}, auth=auth)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["total_tracks"] == 3
    assert len(data["playlist"]) == 3
    assert len(data["transitions"]) >= 2

    # 2. GET /api/set-builder/m3u8 Download
    m3u8_resp = client.get("/api/set-builder/m3u8?bpm_min=150&bpm_max=160", auth=auth)
    assert m3u8_resp.status_code == 200
    assert "#EXTM3U" in m3u8_resp.text
    assert "audio/t1.wav" in m3u8_resp.text or "t1.wav" in m3u8_resp.text
    assert "attachment" in m3u8_resp.headers.get("content-disposition", "")


def test_mix_page_contains_set_builder_ui(tmp_path: Path):
    cfg = make_test_cfg(tmp_path)
    client = TestClient(create_app(cfg, password=PASSWORD))
    auth = ("sc", PASSWORD)

    resp = client.get("/mix", auth=auth)
    assert resp.status_code == 200
    assert "Harmonischer Set-Builder" in resp.text
    assert "set-builder" in resp.text
