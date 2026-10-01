"""Akzeptanztests: SoundCloud-Trend-Radar (Teil 1 von #54)."""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.trends import (
    ArtistGrowth,
    TrackGrowth,
    calculate_artist_trends,
    calculate_track_growth,
    format_trend_report,
)
from tests.test_modes import mk

ROOT = Path(__file__).resolve().parents[2]


def test_track_snapshots_migration_and_recording(tmp_path):
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        # Prüfe Tabelle existiert
        tables = [r[0] for r in db.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "track_snapshots" in tables

        t1 = mk(101, title="Track 1", likes_count=50, playback_count=1000, reposts_count=10, comment_count=2,
                user={"username": "Svetec", "permalink_url": ""})
        t2 = mk(102, title="Track 2", likes_count=100, playback_count=2000, reposts_count=20, comment_count=5,
                user={"username": "O.B.I.", "permalink_url": ""})

        count = db.record_track_snapshots([t1, t2])
        assert count == 2

        rows = db.db.execute("SELECT sc_id, artist, likes FROM track_snapshots ORDER BY sc_id").fetchall()
        assert len(rows) == 2
        assert rows[0]["sc_id"] == 101
        assert rows[0]["artist"] == "Svetec"
        assert rows[0]["likes"] == 50


def test_calculate_track_growth_ranking(tmp_path):
    db_path = tmp_path / "tracks.sqlite"
    now = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
    t_past = (now - timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S")
    t_now = now.strftime("%Y-%m-%d %H:%M:%S")

    with TrackDB(db_path) as db:
        # Track A (ID 1): 50 -> 150 Likes (+200% / +100 Likes)
        db.record_track_snapshot(sc_id=1, artist="Svetec", title="Accelerated", likes=50, plays=1000, recorded_at=t_past)
        db.record_track_snapshot(sc_id=1, artist="Svetec", title="Accelerated", likes=150, plays=3000, recorded_at=t_now)

        # Track B (ID 2): 100 -> 120 Likes (+20% / +20 Likes)
        db.record_track_snapshot(sc_id=2, artist="O.B.I.", title="Distortion", likes=100, plays=2000, recorded_at=t_past)
        db.record_track_snapshot(sc_id=2, artist="O.B.I.", title="Distortion", likes=120, plays=2500, recorded_at=t_now)

        # Track C (ID 3): unter min_initial_likes (z.B. 5 -> 20)
        db.record_track_snapshot(sc_id=3, artist="Newbie", title="Raw", likes=5, plays=100, recorded_at=t_past)
        db.record_track_snapshot(sc_id=3, artist="Newbie", title="Raw", likes=20, plays=400, recorded_at=t_now)

    growth = calculate_track_growth(db_path, days=7, min_initial_likes=10)
    assert len(growth) == 2

    # Track A führt mit +200%
    assert growth[0].sc_id == 1
    assert growth[0].artist == "Svetec"
    assert growth[0].delta_likes == 100
    assert abs(growth[0].growth_rate - 2.0) < 1e-4

    # Track B folgt mit +20%
    assert growth[1].sc_id == 2
    assert growth[1].delta_likes == 20
    assert abs(growth[1].growth_rate - 0.2) < 1e-4


def test_calculate_artist_trends_aggregation(tmp_path):
    db_path = tmp_path / "tracks.sqlite"
    now = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
    t_past = (now - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    t_now = now.strftime("%Y-%m-%d %H:%M:%S")

    with TrackDB(db_path) as db:
        # Svetec: 2 Tracks mit hohem Zuwachs
        # Track 1: 50 -> 100 (+50)
        db.record_track_snapshot(sc_id=1, artist="Svetec", title="T1", likes=50, plays=1000, recorded_at=t_past)
        db.record_track_snapshot(sc_id=1, artist="Svetec", title="T1", likes=100, plays=2000, recorded_at=t_now)
        # Track 2: 50 -> 100 (+50)
        db.record_track_snapshot(sc_id=2, artist="Svetec", title="T2", likes=50, plays=1000, recorded_at=t_past)
        db.record_track_snapshot(sc_id=2, artist="Svetec", title="T2", likes=100, plays=2000, recorded_at=t_now)
        # Gesamt Svetec: Start 100, Delta +100 -> +100%

        # O.B.I.: 1 Track mit geringem Zuwachs
        # Track 3: 100 -> 110 (+10) -> +10%
        db.record_track_snapshot(sc_id=3, artist="O.B.I.", title="T3", likes=100, plays=2000, recorded_at=t_past)
        db.record_track_snapshot(sc_id=3, artist="O.B.I.", title="T3", likes=110, plays=2200, recorded_at=t_now)

    trends = calculate_artist_trends(db_path, days=7, min_initial_likes=20)
    assert len(trends) == 2

    assert trends[0].artist == "Svetec"
    assert trends[0].track_count == 2
    assert trends[0].delta_likes == 100
    assert abs(trends[0].growth_rate - 1.0) < 1e-4

    assert trends[1].artist == "O.B.I."
    assert trends[1].track_count == 1
    assert trends[1].delta_likes == 10
    assert abs(trends[1].growth_rate - 0.1) < 1e-4


def test_calculate_trends_on_missing_or_empty_db(tmp_path):
    missing_db = tmp_path / "nonexistent.sqlite"
    assert calculate_track_growth(missing_db, days=7) == []
    assert calculate_artist_trends(missing_db, days=7) == []

    empty_db = tmp_path / "empty.sqlite"
    with TrackDB(empty_db):
        pass
    assert calculate_track_growth(empty_db, days=7) == []
    assert calculate_artist_trends(empty_db, days=7) == []


def test_format_trend_report_text():
    artists = [
        ArtistGrowth(artist="Svetec", track_count=2, start_likes=100, end_likes=250, delta_likes=150, growth_rate=1.5),
        ArtistGrowth(artist="O.B.I.", track_count=1, start_likes=50, end_likes=75, delta_likes=25, growth_rate=0.5),
    ]
    tracks = [
        TrackGrowth(sc_id=1, artist="Svetec", title="Accelerated", start_likes=50, end_likes=150, delta_likes=100, growth_rate=2.0),
    ]

    report = format_trend_report(artists, tracks, days=7)
    assert "Trend-Radar" in report
    assert "7 Tage" in report
    assert "Svetec" in report
    assert "+150%" in report
    assert "Accelerated" in report
    assert "+200%" in report

    # Leerer Report
    empty_report = format_trend_report([], [], days=7)
    assert "Keine Trends" in empty_report or "keine Trends" in empty_report.lower()


def test_entwicklung_md_contains_trends():
    content = (ROOT / "ENTWICKLUNG.md").read_text(encoding="utf-8")
    assert "sc_digger/trends.py" in content
