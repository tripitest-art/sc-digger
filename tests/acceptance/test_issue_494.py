"""Akzeptanztests für Web-Dashboard: Verfügbarkeits-Wächter auf der Kaufliste (/kaufliste & GET /api/watch)."""
from pathlib import Path

from starlette.testclient import TestClient

from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.database import query_watched_tracks

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


def test_query_watched_tracks_missing_db_returns_empty(tmp_path: Path):
    missing = tmp_path / "does_not_exist.sqlite"
    assert query_watched_tracks(missing) == []
    assert not missing.exists()


def test_query_watched_tracks_sorting_and_metadata(tmp_path: Path):
    _, track_db = _cfg(tmp_path)
    with TrackDB(track_db) as db:
        db.db.execute(
            "INSERT INTO track_snapshots (sc_id, run_at, artist, title, playback_count, likes_count, reposts_count) "
            "VALUES (101, '2026-11-01T10:00:00', 'Artist One', 'Track One', 100, 10, 1)"
        )
        db.db.execute(
            "INSERT INTO track_snapshots (sc_id, run_at, artist, title, playback_count, likes_count, reposts_count) "
            "VALUES (202, '2026-11-01T10:00:00', 'Artist Two', 'Track Two', 200, 20, 2)"
        )
        db.db.execute(
            "INSERT INTO availability_watch (sc_id, first_checked, last_checked, last_kind, available_at) "
            "VALUES (101, '2026-11-01 12:00:00', '2026-11-05 12:00:00', 'none', NULL)"
        )
        db.db.execute(
            "INSERT INTO availability_watch (sc_id, first_checked, last_checked, last_kind, available_at) "
            "VALUES (202, '2026-11-01 12:00:00', '2026-11-06 12:00:00', 'gate', '2026-11-06 12:00:00')"
        )
        db.db.commit()

    items = query_watched_tracks(track_db)
    assert len(items) == 2
    assert items[0]["sc_id"] == 202
    assert items[0]["is_available"] is True
    assert items[0]["artist"] == "Artist Two"
    assert items[0]["title"] == "Track Two"
    assert items[0]["last_kind"] == "gate"
    assert items[1]["sc_id"] == 101
    assert items[1]["is_available"] is False


def test_api_watch_and_kaufliste_section(tmp_path: Path):
    cfg, track_db = _cfg(tmp_path)
    with TrackDB(track_db) as db:
        db.db.execute(
            "INSERT INTO track_snapshots (sc_id, run_at, artist, title, playback_count, likes_count, reposts_count) "
            "VALUES (303, '2026-11-02T10:00:00', 'Schranz Producer', 'Unlocked Tool', 500, 50, 5)"
        )
        db.db.execute(
            "INSERT INTO availability_watch (sc_id, first_checked, last_checked, last_kind, available_at) "
            "VALUES (303, '2026-11-01 12:00:00', '2026-11-06 19:00:00', 'native', '2026-11-06 19:00:00')"
        )
        db.db.commit()

    client = TestClient(create_app(cfg, password=PASSWORD))
    assert client.get("/api/watch").status_code == 401

    resp = client.get("/api/watch", auth=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["available_count"] == 1
    assert data["items"][0]["sc_id"] == 303
    assert data["items"][0]["is_available"] is True

    page = client.get("/kaufliste", auth=AUTH)
    assert page.status_code == 200
    assert "data-watch-section" in page.text
    assert "Schranz Producer" in page.text
    assert "Unlocked Tool" in page.text
    assert "Jetzt verfügbar" in page.text