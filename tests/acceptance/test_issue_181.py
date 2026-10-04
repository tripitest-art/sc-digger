"""Akzeptanztests Issue B zu #54: Plays-Wachstum und Tag-Trends im Trend-Radar.

Snapshots werden mit festem recorded_at in eine echte TrackDB geschrieben.
"""
from __future__ import annotations

import sqlite3

import pytest

from sc_digger import stats
from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.trends import (ArtistGrowth, TagTrend, TrackGrowth, calculate_artist_trends,
                              calculate_tag_trends, calculate_track_growth, format_trend_report)


def _snap(db, sc_id, artist, tags, at, likes, plays=0, reposts=0, comments=0):
    db.record_track_snapshot(sc_id=sc_id, artist=artist, title=f"T{sc_id}", plays=plays,
                             likes=likes, reposts=reposts, comments=comments,
                             recorded_at=at, tags=tags)


def _seed_tags(db):
    # Track 1: Engagement 20 -> 70, Track 2: 40 -> 60 (Engagement = likes + reposts + comments)
    _snap(db, 1, "A", ["hardtechno", "raw"], "2026-10-01 10:00:00", likes=10, reposts=5, comments=5)
    _snap(db, 2, "B", ["hardtechno"], "2026-10-01 10:00:00", likes=30, reposts=5, comments=5)
    _snap(db, 1, "A", ["hardtechno", "raw"], "2026-10-03 10:00:00", likes=50, reposts=10, comments=10)
    _snap(db, 2, "B", ["hardtechno"], "2026-10-03 10:00:00", likes=40, reposts=10, comments=10)


def test_track_growth_reports_delta_plays(tmp_path):
    path = tmp_path / "t.sqlite"
    with TrackDB(path) as db:
        _snap(db, 1, "A", None, "2026-10-01 10:00:00", likes=50, plays=1000)
        _snap(db, 1, "A", None, "2026-10-03 10:00:00", likes=100, plays=4000)
    (g,) = calculate_track_growth(path, days=7, min_initial_likes=10)
    assert (g.delta_likes, g.delta_plays) == (50, 3000)


def test_artist_trends_report_delta_plays(tmp_path):
    path = tmp_path / "t.sqlite"
    with TrackDB(path) as db:
        _snap(db, 1, "A", None, "2026-10-01 10:00:00", likes=10, plays=100)
        _snap(db, 1, "A", None, "2026-10-03 10:00:00", likes=20, plays=400)
        _snap(db, 2, "A", None, "2026-10-01 10:00:00", likes=10, plays=50)
        _snap(db, 2, "A", None, "2026-10-03 10:00:00", likes=30, plays=150)
    (a,) = calculate_artist_trends(path, days=7, min_initial_likes=10)
    assert (a.delta_likes, a.delta_plays) == (30, 400)


def test_existing_dataclass_constructors_still_work():
    assert TrackGrowth(1, "A", "T", 50, 150, 100, 2.0).delta_plays == 0
    assert ArtistGrowth("A", 1, 50, 150, 100, 2.0).delta_plays == 0


def test_tag_trends_volume_engagement_and_growth(tmp_path):
    path = tmp_path / "t.sqlite"
    with TrackDB(path) as db:
        _seed_tags(db)
    trends = calculate_tag_trends(path, days=7)
    assert [t.tag for t in trends] == ["hardtechno"]  # "raw" hat nur 1 Track (min_tracks=2)
    t = trends[0]
    assert t.track_count == 2
    assert t.avg_engagement == pytest.approx(65.0)  # (70 + 60) / 2 am Ende des Fensters
    assert t.delta_engagement == pytest.approx(70.0)  # 130 - 60
    assert t.growth_rate == pytest.approx(70.0 / 60.0)


def test_tag_trends_min_tracks_and_sort_order(tmp_path):
    path = tmp_path / "t.sqlite"
    with TrackDB(path) as db:
        _seed_tags(db)
    trends = calculate_tag_trends(path, days=7, min_tracks=1)
    assert [t.tag for t in trends] == ["raw", "hardtechno"]  # raw: +250 %, hardtechno: +117 %


def test_tag_trends_uses_tags_of_newest_snapshot_and_normalizes(tmp_path):
    path = tmp_path / "t.sqlite"
    with TrackDB(path) as db:
        _snap(db, 1, "A", ["Alt"], "2026-10-01 10:00:00", likes=30)
        _snap(db, 1, "A", ["HardTechno ", "hardtechno"], "2026-10-03 10:00:00", likes=60)
        _snap(db, 2, "B", ["hardtechno"], "2026-10-01 10:00:00", likes=30)
        _snap(db, 2, "B", ["hardtechno"], "2026-10-03 10:00:00", likes=40)
    trends = calculate_tag_trends(path, days=7, min_tracks=1)
    assert [t.tag for t in trends] == ["hardtechno"]  # "alt" nicht mehr, Duplikate zählen einmal
    assert trends[0].track_count == 2


def test_tag_trends_tolerate_bad_rows_and_missing_db(tmp_path):
    path = tmp_path / "t.sqlite"
    with TrackDB(path) as db:
        _snap(db, 3, "C", None, "2026-10-01 10:00:00", likes=50)
        _snap(db, 3, "C", None, "2026-10-03 10:00:00", likes=90)
        db.db.execute(
            "INSERT INTO track_snapshots (sc_id, artist, likes, recorded_at, tags) "
            "VALUES (9, 'X', 40, '2026-10-02 10:00:00', 'kein-json{')")
        db.db.commit()
    assert calculate_tag_trends(path, days=7, min_tracks=1) == []
    assert calculate_tag_trends(tmp_path / "fehlt.sqlite") == []


def test_tag_trends_work_on_database_without_tags_column(tmp_path):
    path = tmp_path / "alt.sqlite"
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE track_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, sc_id INTEGER, "
                "artist TEXT, title TEXT, plays INTEGER, likes INTEGER, reposts INTEGER, "
                "comments INTEGER, recorded_at TEXT)")
    con.execute("INSERT INTO track_snapshots (sc_id, artist, likes, recorded_at) "
                "VALUES (1, 'A', 50, '2026-10-01 10:00:00')")
    con.commit()
    con.close()
    assert calculate_tag_trends(path, days=7, min_tracks=1) == []


def test_format_trend_report_lines():
    tags = [TagTrend(tag="hardtechno", track_count=2, avg_engagement=65.0,
                     delta_engagement=70.0, growth_rate=70.0 / 60.0)]
    artists = [ArtistGrowth("X", 1, 100, 150, 50, 0.5, delta_plays=400)]
    tracks = [TrackGrowth(1, "Y", "T", 10, 20, 10, 1.0, delta_plays=300)]
    report = format_trend_report(artists, tracks, days=7, tag_trends=tags)
    assert "🏷️ Trending Tags" in report
    assert "1. hardtechno — +117% (⌀ 65 Engagement, 2 Tracks)" in report
    assert "1. X — +50% (+50 Likes, +400 Plays, 1 Track)" in report
    assert "1. Y – T: +100% (+10 Likes, +300 Plays)" in report


def test_format_trend_report_unchanged_without_plays_and_tags():
    artists = [ArtistGrowth("X", 2, 100, 150, 50, 0.5)]
    tracks = [TrackGrowth(1, "Y", "T", 10, 20, 10, 1.0)]
    report = format_trend_report(artists, tracks, days=7)
    assert "1. X — +50% (+50 Likes, 2 Tracks)" in report
    assert "1. Y – T: +100% (+10 Likes)" in report
    assert "Trending Tags" not in report
    assert format_trend_report([], [], days=7, tag_trends=[]) == "Keine Trends im Zeitraum erkannt."


def test_trend_section_contains_tag_block(tmp_path):
    path = tmp_path / "t.sqlite"
    with TrackDB(path) as db:
        _seed_tags(db)
    cfg = Config({"state": {"track_db_path": str(path)}, "digest": {}})
    assert "🏷️ Trending Tags" in stats._trend_section(cfg)
