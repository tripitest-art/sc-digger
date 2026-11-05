"""Akzeptanztests: 1-Klick Ähnliche Tracks finden (⚡ Match-Button in /tracks -> /mix)."""
from __future__ import annotations

from pathlib import Path

from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from sc_digger.web.app import create_app, match_url
from tests.test_web_tracks_ui import TestClient

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "test-match-btn-pw"
AUTH = ("sc", PASSWORD)


def _make_cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def test_match_url_helper_builds_mix_query_or_none():
    assert match_url("8a", 155.0) == "/mix?key=8A&bpm=155"
    assert match_url("11B", 152.5) == "/mix?key=11B&bpm=152.5"
    assert match_url(None, 155.0) is None
    assert match_url("", 155.0) is None
    assert match_url("8A", None) is None
    assert match_url("8A", 0.0) is None
    assert match_url("8A", -120.0) is None


def test_tracks_page_renders_match_button_only_for_tracks_with_key_and_bpm(tmp_path: Path):
    cfg = _make_cfg(tmp_path)
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        db.upsert_track(TrackRecord(
            path="inbox/155/mit_key.wav", mtime=1.0, size=100,
            artist="Svetec", title="Mit Key", format="wav",
            quality_status=QualityStatus.OK, bpm=155.0, key_camelot="8A",
            status=TrackStatus.INBOX,
        ))
        db.upsert_track(TrackRecord(
            path="inbox/ohne_key.wav", mtime=2.0, size=100,
            artist="Unbekannt", title="Ohne Key", format="wav",
            quality_status=QualityStatus.OK, bpm=150.0, key_camelot=None,
            status=TrackStatus.INBOX,
        ))

    client = TestClient(create_app(cfg, password=PASSWORD))
    resp = client.get("/tracks", auth=AUTH)
    assert resp.status_code == 200
    assert 'href="/mix?key=8A&amp;bpm=155"' in resp.text or 'href="/mix?key=8A&bpm=155"' in resp.text
    assert "⚡ Match" in resp.text
    assert resp.text.count("match-btn") >= 1

    # Klickziel /mix?key=8A&bpm=155 liefert direkt passende Kandidaten
    mix_resp = client.get("/mix?key=8A&bpm=155", auth=AUTH)
    assert mix_resp.status_code == 200
    assert "Mit Key" in mix_resp.text
