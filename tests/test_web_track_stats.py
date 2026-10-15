"""Eigene Tests zu Engagement- und Rekordbox-Kennzahlen im Track-Explorer (Issue #258).

Ergänzen den Akzeptanztest um den Beweis, dass je Track der *jüngste* Snapshot zählt,
dass ein fehlender Snapshot-Index/Bestand ``None`` statt eines Fehlers liefert und
dass die Oberfläche die Meta-Zeile nur bei vorhandenen Zahlen rendert.
"""
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.database import query_tracks

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-258"

TRACK_WITH_SC = "/music/Schranz/150/8A/[999] Artist - Titel.wav"
TRACK_WITHOUT_SC = "/music/Schranz/155/9A/Ohne Quelle.flac"


def make_cfg(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def seed(tmp_path):
    """Zwei Tracks (einer mit SoundCloud-ID) und zwei Snapshots zum selben Track."""
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.upsert_track(TrackRecord(
            path=TRACK_WITH_SC, mtime=1.0, size=1000,
            artist="Artist", title="Titel", format="wav", bitrate_kbps=1411.0,
            quality_status=QualityStatus.OK, bpm=150.0, key_camelot="8A",
            status=TrackStatus.ARCHIVE, rb_play_count=4,
        ))
        db.upsert_track(TrackRecord(
            path=TRACK_WITHOUT_SC, mtime=2.0, size=2000,
            artist="Ohne", title="Quelle", format="flac", bitrate_kbps=900.0,
            quality_status=QualityStatus.OK, bpm=155.0, key_camelot="9A",
            status=TrackStatus.INBOX,
        ))
        # Älterer und jüngerer Snapshot: der jüngere muss gewinnen (recorded_at entscheidet).
        db.record_track_snapshot(
            999, "Artist", "Titel", plays=100, likes=10, reposts=1, comments=0,
            recorded_at="2026-10-10 12:00:00",
        )
        db.record_track_snapshot(
            999, "Artist", "Titel", plays=5600, likes=420, reposts=30, comments=8,
            recorded_at="2026-10-14 12:00:00",
        )
    return db_path


def by_id(items, track_id):
    return [i for i in items if i["id"] == track_id][0]


# ------------------------------------------------------------------ Abfrage-Schicht
def test_query_tracks_uses_newest_snapshot_per_track(tmp_path):
    db_path = seed(tmp_path)
    res = query_tracks(db_path)
    with_sc = [i for i in res.items if i["path"] == TRACK_WITH_SC][0]
    without_sc = [i for i in res.items if i["path"] == TRACK_WITHOUT_SC][0]

    assert with_sc["sc_plays"] == 5600          # nicht 100: der jüngere Snapshot zählt
    assert with_sc["sc_likes"] == 420
    assert with_sc["sc_reposts"] == 30
    assert with_sc["sc_comments"] == 8
    assert with_sc["rb_play_count"] == 4

    assert without_sc["sc_plays"] is None       # keine SoundCloud-ID im Dateinamen
    assert without_sc["sc_likes"] is None
    assert without_sc["sc_reposts"] is None
    assert without_sc["sc_comments"] is None
    assert without_sc["rb_play_count"] is None


def test_query_tracks_without_snapshot_rows_is_none(tmp_path):
    db_path = seed(tmp_path)
    with sqlite3.connect(db_path) as db:
        db.execute("DELETE FROM track_snapshots")
    with_sc = [i for i in query_tracks(db_path).items if i["path"] == TRACK_WITH_SC][0]
    assert with_sc["sc_plays"] is None
    assert with_sc["sc_likes"] is None
    assert with_sc["rb_play_count"] == 4        # Rekordbox-Zahl bleibt unabhängig erhalten


def test_query_tracks_without_snapshot_table_is_none(tmp_path):
    db_path = seed(tmp_path)
    with sqlite3.connect(db_path) as db:
        db.execute("DROP TABLE track_snapshots")
    res = query_tracks(db_path)
    assert res.total == 2                       # kein Absturz an der fehlenden Tabelle
    assert all(i["sc_plays"] is None for i in res.items)


def test_query_tracks_stats_do_not_leak_into_other_tracks(tmp_path):
    db_path = seed(tmp_path)
    items = query_tracks(db_path).items
    assert by_id(items, 1)["sc_plays"] == 5600
    assert by_id(items, 2)["sc_plays"] is None


# ------------------------------------------------------------------ Oberfläche
def test_tracks_page_shows_engagement_and_play_badge(tmp_path):
    seed(tmp_path)
    client = TestClient(create_app(make_cfg(tmp_path), password=PASSWORD))
    r = client.get("/tracks", auth=("sc", PASSWORD))
    assert r.status_code == 200
    assert "▶ 5600 · ♥ 420 · 🔁 30" in r.text
    assert "🎧 4x im Set" in r.text
    # Nur der Track mit Plays bekommt das Badge, der ohne Quelle keine Meta-Zeile.
    assert r.text.count("im Set") == 1
    assert r.text.count("▶ ") == 1


def test_tracks_page_hides_meta_line_without_stats(tmp_path):
    seed(tmp_path)
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        db.execute("DROP TABLE track_snapshots")
        db.execute("UPDATE tracks SET rb_play_count = NULL")
    client = TestClient(create_app(make_cfg(tmp_path), password=PASSWORD))
    r = client.get("/tracks", auth=("sc", PASSWORD))
    assert r.status_code == 200
    assert "▶ " not in r.text
    assert "im Set" not in r.text
