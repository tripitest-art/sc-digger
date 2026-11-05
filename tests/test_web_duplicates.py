"""Eigene Tests für die Duplikat-Kandidaten-Ansicht (/duplicates & /api/duplicates)."""
from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.fingerprint import encode_fingerprint
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.database import query_duplicate_groups
from tests.test_web_tracks_ui import TestClient

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "secret-dup-test"
AUTH = ("sc", PASSWORD)


def _make_cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def test_query_duplicate_groups_empty_and_edge_cases(tmp_path: Path):
    # Datei existiert nicht
    missing = tmp_path / "missing.sqlite"
    assert query_duplicate_groups(missing) == []
    assert not missing.exists()

    # DB existiert, aber keine tracks-Tabelle
    empty_db = tmp_path / "empty.sqlite"
    conn = sqlite3.connect(empty_db)
    conn.execute("CREATE TABLE other (id INTEGER PRIMARY KEY)")
    conn.close()
    assert query_duplicate_groups(empty_db) == []

    # tracks-Tabelle existiert, aber leer
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        pass
    assert query_duplicate_groups(db_path) == []

    # Nur ein Track mit Fingerprint -> keine Gruppe
    fp = encode_fingerprint([0x12345678] * 80)
    with TrackDB(db_path) as db:
        db.upsert_track(TrackRecord(
            path="archive/solo.wav", mtime=1.0, size=1000,
            fingerprint=fp, fingerprint_duration=200.0,
        ))
    assert query_duplicate_groups(db_path) == []

    # Track ohne duration oder leerer fingerprint -> ignoriert
    with TrackDB(db_path) as db:
        db.upsert_track(TrackRecord(
            path="archive/no_fp.wav", mtime=1.0, size=1000,
            fingerprint="", fingerprint_duration=200.0,
        ))
        db.upsert_track(TrackRecord(
            path="archive/no_dur.wav", mtime=1.0, size=1000,
            fingerprint=fp, fingerprint_duration=None,
        ))
    assert query_duplicate_groups(db_path) == []


def test_query_duplicate_groups_multiple_groups(tmp_path: Path):
    db_path = tmp_path / "multi.sqlite"
    fp1 = encode_fingerprint([0xAAAAAAAA] * 80)
    fp2 = encode_fingerprint([0x55555555] * 80)

    with TrackDB(db_path) as db:
        # Gruppe 1 (A & B)
        db.upsert_track(TrackRecord(
            path="archive/track_a1.wav", mtime=1.0, size=1000,
            artist="Artist 1", title="Track A (WAV)", format="wav", bitrate_kbps=1411.2,
            bpm=150.0, key_camelot="5A", status=TrackStatus.ARCHIVE,
            fingerprint=fp1, fingerprint_duration=300.0,
        ))
        db.upsert_track(TrackRecord(
            path="inbox/track_a2.mp3", mtime=2.0, size=500,
            artist="Artist 1", title="Track A (MP3)", format="mp3", bitrate_kbps=320.0,
            bpm=150.0, key_camelot="5A", status=TrackStatus.INBOX,
            fingerprint=fp1, fingerprint_duration=300.2,
        ))
        # Gruppe 2 (C & D)
        db.upsert_track(TrackRecord(
            path="archive/track_b1.flac", mtime=3.0, size=800,
            artist="Artist 2", title="Track B (FLAC)", format="flac", bitrate_kbps=900.0,
            bpm=155.0, key_camelot="8A", status=TrackStatus.ARCHIVE,
            fingerprint=fp2, fingerprint_duration=250.0,
        ))
        db.upsert_track(TrackRecord(
            path="inbox/track_b2.wav", mtime=4.0, size=1200,
            artist="Artist 2", title="Track B (WAV)", format="wav", bitrate_kbps=1411.2,
            bpm=155.0, key_camelot="8A", status=TrackStatus.INBOX,
            fingerprint=fp2, fingerprint_duration=250.1,
        ))

    groups = query_duplicate_groups(db_path)
    assert len(groups) == 2
    assert groups[0]["count"] == 2
    assert groups[1]["count"] == 2

    track_0 = groups[0]["tracks"][0]
    assert "id" in track_0
    assert "path" in track_0
    assert "artist" in track_0
    assert "title" in track_0
    assert "bpm" in track_0
    assert "key_camelot" in track_0
    assert "format" in track_0
    assert "bitrate_kbps" in track_0
    assert "status" in track_0


def test_api_and_page_empty_state(tmp_path: Path):
    cfg = _make_cfg(tmp_path)
    client = TestClient(create_app(cfg, password=PASSWORD))

    # API bei 0 Gruppen
    resp = client.get("/api/duplicates", auth=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "total_groups": 0, "groups": []}

    # HTML bei 0 Gruppen
    html_resp = client.get("/duplicates", auth=AUTH)
    assert html_resp.status_code == 200
    assert "Keine Duplikat-Gruppen" in html_resp.text
    assert "<strong>0</strong>" in html_resp.text


def test_tracks_toolbar_contains_duplicates_link(tmp_path: Path):
    cfg = _make_cfg(tmp_path)
    client = TestClient(create_app(cfg, password=PASSWORD))

    resp = client.get("/tracks", auth=AUTH)
    assert resp.status_code == 200
    assert 'href="/duplicates"' in resp.text
