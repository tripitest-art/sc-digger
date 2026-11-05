"""Akzeptanztests: Duplikat-Kandidaten-Ansicht im Web-Dashboard (/duplicates & /api/duplicates)."""
from __future__ import annotations

from pathlib import Path

from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.fingerprint import encode_fingerprint
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.database import query_duplicate_groups
from tests.test_web_tracks_ui import TestClient

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "test-duplicates-pw"
AUTH = ("sc", PASSWORD)


def _make_cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def test_query_duplicate_groups_missing_db_stays_readonly(tmp_path: Path):
    missing_db = tmp_path / "nicht_vorhanden.sqlite"
    assert query_duplicate_groups(missing_db) == []
    assert not missing_db.exists()


def test_duplicates_api_and_html_list_fingerprint_groups(tmp_path: Path):
    cfg = _make_cfg(tmp_path)
    db_path = Path(cfg["state"]["track_db_path"])

    fp_same = encode_fingerprint([0x12345678] * 80)
    fp_other = encode_fingerprint([0xFFFFFFFF] * 80)

    with TrackDB(db_path) as db:
        db.upsert_track(TrackRecord(
            path="archive/svetec_original.wav", mtime=1.0, size=5000,
            artist="Svetec", title="Hammer", format="wav", bitrate_kbps=1411.0,
            quality_status=QualityStatus.OK, bpm=155.0, key_camelot="8A",
            status=TrackStatus.ARCHIVE, fingerprint=fp_same, fingerprint_duration=300.0,
        ))
        db.upsert_track(TrackRecord(
            path="inbox/svetec_reupload.mp3", mtime=2.0, size=1200,
            artist="Svetec", title="Hammer (Free DL)", format="mp3", bitrate_kbps=320.0,
            quality_status=QualityStatus.OK, bpm=155.0, key_camelot="8A",
            status=TrackStatus.INBOX, fingerprint=fp_same, fingerprint_duration=300.5,
        ))
        db.upsert_track(TrackRecord(
            path="archive/unikat.flac", mtime=3.0, size=4000,
            artist="Kobosil", title="Unikat", format="flac", bitrate_kbps=900.0,
            quality_status=QualityStatus.OK, bpm=150.0, key_camelot="4A",
            status=TrackStatus.ARCHIVE, fingerprint=fp_other, fingerprint_duration=280.0,
        ))

    groups = query_duplicate_groups(db_path)
    assert len(groups) == 1
    assert groups[0]["count"] == 2
    paths = [t["path"] for t in groups[0]["tracks"]]
    assert paths == ["archive/svetec_original.wav", "inbox/svetec_reupload.mp3"]

    client = TestClient(create_app(cfg, password=PASSWORD))

    # Ohne Auth -> 401
    assert client.get("/duplicates").status_code == 401
    assert client.get("/api/duplicates").status_code == 401

    # JSON-API
    api_resp = client.get("/api/duplicates", auth=AUTH)
    assert api_resp.status_code == 200
    payload = api_resp.json()
    assert payload["ok"] is True
    assert payload["total_groups"] == 1
    assert len(payload["groups"][0]["tracks"]) == 2

    # HTML-Ansicht
    html_resp = client.get("/duplicates", auth=AUTH)
    assert html_resp.status_code == 200
    assert "archive/svetec_original.wav" in html_resp.text
    assert "inbox/svetec_reupload.mp3" in html_resp.text
    assert "Unikat" not in html_resp.text
