from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord, TrackStatus, QualityStatus
from sc_digger.models import Config
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "test-pw-123"

def make_cfg(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg

def seed_db(tmp_path):
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.upsert_track(TrackRecord(
            path="/music/Schranz/track1.wav", mtime=1.0, size=1000,
            artist="Svetec", title="Acid Kick", format="wav", bitrate_kbps=1411.0,
            cutoff_hz=21000, quality_status=QualityStatus.OK, bpm=150.0,
            key_camelot="8A", status=TrackStatus.ARCHIVE, source_url="https://soundcloud.com/svetec/1"
        ))
        db.upsert_track(TrackRecord(
            path="/music/inbox/track2.mp3", mtime=2.0, size=500,
            artist="Viper XXL", title="Hard Groove", format="mp3", bitrate_kbps=320.0,
            cutoff_hz=19500, quality_status=QualityStatus.OK, bpm=155.0,
            key_camelot="9A", status=TrackStatus.INBOX, adopted_at="2026-10-10 14:00:00"
        ))
    return db_path

def test_tracks_route_requires_auth(tmp_path):
    seed_db(tmp_path)
    client = TestClient(create_app(make_cfg(tmp_path), password=PASSWORD))
    r = client.get("/tracks")
    assert r.status_code == 401
    assert "basic" in r.headers["www-authenticate"].lower()

def test_tracks_route_renders_html_with_tracks_and_badges(tmp_path):
    seed_db(tmp_path)
    client = TestClient(create_app(make_cfg(tmp_path), password=PASSWORD))
    r = client.get("/tracks", auth=("sc", PASSWORD))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "Svetec" in r.text
    assert "Acid Kick" in r.text
    assert "Viper XXL" in r.text
    assert "Hard Groove" in r.text
    assert "150" in r.text
    assert "8A" in r.text
    assert "https://soundcloud.com/svetec/1" in r.text

def test_tracks_route_filters_by_query_param(tmp_path):
    seed_db(tmp_path)
    client = TestClient(create_app(make_cfg(tmp_path), password=PASSWORD))
    r = client.get("/tracks?q=Viper", auth=("sc", PASSWORD))
    assert r.status_code == 200
    assert "Viper XXL" in r.text
    assert "Svetec" not in r.text

def test_base_navigation_contains_tracks_link(tmp_path):
    client = TestClient(create_app(make_cfg(tmp_path), password=PASSWORD))
    r = client.get("/", auth=("sc", PASSWORD))
    assert r.status_code == 200
    assert 'href="/tracks"' in r.text
