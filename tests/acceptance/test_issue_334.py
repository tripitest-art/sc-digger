"""Akzeptanztests: Visuelle Cue- und Struktur-Werte im Track-Explorer (Issue #334)."""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.database import query_tracks

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "test-tracks-structure"


def make_test_cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def test_query_tracks_breakdown_ratio_field_and_filter(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        # Tool: 10% Breakdown
        db.upsert_track(TrackRecord(
            path="audio/tool.wav", mtime=1.0, size=10, status="archive",
            artist="Artist 1", title="Peaktime Tool", bpm=155.0, breakdown_ratio=0.10,
        ))
        # Melodic: 35% Breakdown
        db.upsert_track(TrackRecord(
            path="audio/break.wav", mtime=1.0, size=10, status="archive",
            artist="Artist 2", title="Epic Breakdown", bpm=155.0, breakdown_ratio=0.35,
        ))
        # Unbekannt: None
        db.upsert_track(TrackRecord(
            path="audio/unknown.wav", mtime=1.0, size=10, status="archive",
            artist="Artist 3", title="Unknown Track", bpm=155.0, breakdown_ratio=None,
        ))

    # 1. Ohne Filter: breakdown_ratio ist in items enthalten
    all_res = query_tracks(db_path)
    assert all_res.total == 3
    tool_item = next(it for it in all_res.items if it["title"] == "Peaktime Tool")
    assert tool_item["breakdown_ratio"] == pytest.approx(0.10, abs=0.001)

    # 2. Mit Filter breakdown_ratio_max=0.20: nur Tool gefunden
    filtered = query_tracks(db_path, breakdown_ratio_max=0.20)
    assert filtered.total == 1
    assert filtered.items[0]["title"] == "Peaktime Tool"


def test_tracks_page_renders_breakdown_badge_and_handles_filter(tmp_path: Path):
    cfg = make_test_cfg(tmp_path)
    db_path = cfg["state"]["track_db_path"]
    with TrackDB(db_path) as db:
        db.upsert_track(TrackRecord(
            path="audio/roller.wav", mtime=1.0, size=10, status="archive",
            artist="Svetec", title="Hard Roller", bpm=156.0, breakdown_ratio=0.12,
        ))

    client = TestClient(create_app(cfg, password=PASSWORD))
    auth = ("sc", PASSWORD)

    # HTML-Seite abrufen
    resp = client.get("/tracks", auth=auth)
    assert resp.status_code == 200
    assert "12%" in resp.text
    assert "breakdown_max" in resp.text

    # Gefilterte Abfrage
    resp_filtered = client.get("/tracks?breakdown_max=0.15", auth=auth)
    assert resp_filtered.status_code == 200
    assert "Hard Roller" in resp_filtered.text

    # Filter schließt Track aus
    resp_excluded = client.get("/tracks?breakdown_max=0.05", auth=auth)
    assert resp_excluded.status_code == 200
    assert "Hard Roller" not in resp_excluded.text
