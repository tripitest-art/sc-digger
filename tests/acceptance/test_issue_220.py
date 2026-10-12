from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from sc_digger.db import TrackDB, TrackRecord, TrackStatus, QualityStatus
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.database import get_collection_overview, query_tracks, CollectionOverview, TrackSearchResult

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
        db.upsert_track(TrackRecord(
            path="/music/rejected/track3.mp3", mtime=3.0, size=200,
            artist="Fake DJ", title="Low Quality", format="mp3", bitrate_kbps=128.0,
            cutoff_hz=14000, quality_status=QualityStatus.FAKE_TRANSCODE, bpm=140.0,
            status=TrackStatus.REJECTED
        ))
    return db_path

def test_collection_overview(tmp_path):
    db_path = seed_db(tmp_path)
    overview = get_collection_overview(db_path)
    assert overview.total_tracks == 3
    assert overview.archive_count == 1
    assert overview.inbox_count == 1
    assert overview.rejected_count == 1
    assert overview.adopted_count == 1
    assert overview.quality_counts.get("ok") == 2
    assert overview.quality_counts.get("fake_transcode") == 1
    assert overview.format_counts.get("wav") == 1
    assert overview.format_counts.get("mp3") == 2

def test_query_tracks_filtering(tmp_path):
    db_path = seed_db(tmp_path)
    
    # Filter nach Status
    res = query_tracks(db_path, status="inbox")
    assert res.total == 1
    assert res.items[0]["artist"] == "Viper XXL"
    
    # Filter nach Suchbegriff
    res = query_tracks(db_path, search="Acid")
    assert res.total == 1
    assert res.items[0]["title"] == "Acid Kick"
    
    # Filter nach BPM-Bereich
    res = query_tracks(db_path, bpm_min=152.0)
    assert res.total == 1
    assert res.items[0]["bpm"] == 155.0

    # Filter nach Camelot Key
    res = query_tracks(db_path, key_camelot="8A")
    assert res.total == 1
    assert res.items[0]["artist"] == "Svetec"

    # Filter nach Rekordbox-Übernahme (adopted)
    res_adopted = query_tracks(db_path, adopted=True)
    assert res_adopted.total == 1
    assert res_adopted.items[0]["artist"] == "Viper XXL"

def test_api_database_endpoints_auth_and_json(tmp_path):
    seed_db(tmp_path)
    client = TestClient(create_app(make_cfg(tmp_path), password=PASSWORD))
    
    # 401 ohne Auth
    assert client.get("/api/database/overview").status_code == 401
    assert client.get("/api/tracks").status_code == 401
    
    # Overview JSON
    r = client.get("/api/database/overview", auth=("sc", PASSWORD))
    assert r.status_code == 200
    data = r.json()
    assert data["total_tracks"] == 3
    assert data["inbox_count"] == 1
    assert data["adopted_count"] == 1
    
    # Tracks JSON mit Query
    r = client.get("/api/tracks?q=Viper&status=inbox", auth=("sc", PASSWORD))
    assert r.status_code == 200
    t_data = r.json()
    assert t_data["total"] == 1
    assert t_data["items"][0]["artist"] == "Viper XXL"

def test_missing_db_returns_empty_gracefully(tmp_path):
    missing_path = tmp_path / "does_not_exist.sqlite"
    overview = get_collection_overview(missing_path)
    assert overview.total_tracks == 0
    assert overview.archive_count == 0
    
    res = query_tracks(missing_path)
    assert res.total == 0
    assert res.items == []
