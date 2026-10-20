"""Akzeptanztests: Quarantäne- und Rejection-Manager im Web-Dashboard (Issue #341)."""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from sc_digger.models import Config, Track
from sc_digger.output import State
from sc_digger.web.app import create_app
from sc_digger.web.database import query_rejected_tracks, retry_quarantined_track

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "test-rejected-secret"


def make_test_cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def _make_track(track_id: int, title: str, url: str) -> Track:
    return Track(
        id=track_id, title=title, url=url, artist="Test Artist",
        artist_url="", created_at="", duration_ms=180000, genre="Schranz",
        tags=[], description="", bpm=150.0, plays=0, likes=0, reposts=0,
        comments=0, downloadable=True, has_downloads_left=True,
        purchase_url=None, purchase_title=None,
    )


def test_query_and_retry_rejected_tracks(tmp_path: Path):
    cfg = make_test_cfg(tmp_path)
    state_db = cfg["state"]["db_path"]

    with State(state_db) as state:
        t1 = _make_track(501, "Fake Transcode 1", "https://soundcloud.com/t1")
        t2 = _make_track(502, "Brickwall Master 2", "https://soundcloud.com/t2")
        state.mark_rejected(t1, reason="Spektrum endet bei 14 kHz", cutoff_hz=14000.0)
        state.mark_rejected(t2, reason="Clipping True Peak +4.0", cutoff_hz=21000.0)

    # 1. Auslesen
    res = query_rejected_tracks(state_db)
    assert res["total"] == 2
    assert any(it["id"] == 501 for it in res["items"])

    # 2. Suche
    search_res = query_rejected_tracks(state_db, search="Brickwall")
    assert search_res["total"] == 1
    assert search_res["items"][0]["id"] == 502

    # 3. Retry über Funktion
    retried = retry_quarantined_track(state_db, 501)
    assert retried is True

    # 4. Prüfen nach Retry
    after_res = query_rejected_tracks(state_db)
    assert after_res["total"] == 1
    assert after_res["items"][0]["id"] == 502


def test_rejected_web_routes(tmp_path: Path):
    cfg = make_test_cfg(tmp_path)
    state_db = cfg["state"]["db_path"]

    with State(state_db) as state:
        t = _make_track(601, "Quarantined Sound", "https://soundcloud.com/q")
        state.mark_rejected(t, reason="Fake Spektrum", cutoff_hz=13500.0)

    client = TestClient(create_app(cfg, password=PASSWORD))
    auth = ("sc", PASSWORD)

    # GET /rejected (HTML)
    page_resp = client.get("/rejected", auth=auth)
    assert page_resp.status_code == 200
    assert "Quarantined Sound" in page_resp.text
    assert "13500" in page_resp.text or "13.5" in page_resp.text

    # POST /api/retry
    retry_resp = client.post(
        "/api/retry",
        json={"track_id": 601},
        auth=auth,
        headers={"origin": "http://testserver"},
    )
    assert retry_resp.status_code == 200
    assert retry_resp.json()["ok"] is True

    # Verifizieren, dass Track weg ist
    res = query_rejected_tracks(state_db)
    assert res["total"] == 0
