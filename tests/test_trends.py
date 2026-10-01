"""Eigene Tests für sc_digger/trends.py (Randfälle; Akzeptanztests siehe tests/acceptance/)."""
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace

from sc_digger.db import TrackDB
from sc_digger.trends import (
    ArtistGrowth,
    TrackGrowth,
    calculate_artist_trends,
    calculate_track_growth,
    format_trend_report,
)

BASE = datetime(2026, 9, 30, 12, 0, 0)


def ts(days_ago: int) -> str:
    return (BASE - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _record(db, sc_id, artist, likes, days_ago, title="T"):
    db.record_track_snapshot(
        sc_id=sc_id, artist=artist, title=title, likes=likes, plays=likes * 10, recorded_at=ts(days_ago)
    )


# ---------------- Aufzeichnung ----------------
def test_record_track_snapshots_skips_objects_without_id(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        no_id = SimpleNamespace(artist="Ohne", title="x", likes=5, plays=5, reposts=0, comments=0)
        with_id = SimpleNamespace(id=42, artist="Mit", title="y", likes=7, plays=70, reposts=1, comments=2)
        assert db.record_track_snapshots([no_id, with_id]) == 1
        rows = db.db.execute("SELECT sc_id, plays, reposts, comments FROM track_snapshots").fetchall()
        assert len(rows) == 1
        assert rows[0]["sc_id"] == 42
        assert rows[0]["plays"] == 70
        assert rows[0]["reposts"] == 1


def test_record_track_snapshots_prefers_sc_id_attribute(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        obj = SimpleNamespace(sc_id=7, id=99, artist="A", title="t", likes=1, plays=1)
        assert db.record_track_snapshots([obj]) == 1
        assert db.db.execute("SELECT sc_id FROM track_snapshots").fetchone()[0] == 7


def test_record_track_snapshot_default_timestamp(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        db.record_track_snapshot(sc_id=1, artist="A", likes=3)
        row = db.db.execute("SELECT recorded_at, likes, plays FROM track_snapshots").fetchone()
        assert row["recorded_at"]  # CURRENT_TIMESTAMP gesetzt
        assert row["likes"] == 3
        assert row["plays"] == 0


# ---------------- Wachstum ----------------
def test_track_growth_uses_oldest_and_newest_within_window(tmp_path):
    db_path = tmp_path / "t.sqlite"
    with TrackDB(db_path) as db:
        _record(db, 1, "A", 10, 10)   # außerhalb des 7-Tage-Fensters -> ignoriert
        _record(db, 1, "A", 20, 6)    # ältester Snapshot im Fenster
        _record(db, 1, "A", 40, 0)    # jüngster
    growth = calculate_track_growth(db_path, days=7, min_initial_likes=10)
    assert len(growth) == 1
    assert growth[0].start_likes == 20
    assert growth[0].end_likes == 40
    assert growth[0].delta_likes == 20
    assert abs(growth[0].growth_rate - 1.0) < 1e-9


def test_track_growth_zero_likes_no_division_by_zero(tmp_path):
    db_path = tmp_path / "t.sqlite"
    with TrackDB(db_path) as db:
        _record(db, 1, "A", 0, 3)
        _record(db, 1, "A", 5, 0)
    growth = calculate_track_growth(db_path, days=7, min_initial_likes=0)
    assert len(growth) == 1
    assert growth[0].growth_rate == 0.0


def test_track_growth_negative_days_returns_empty(tmp_path):
    db_path = tmp_path / "t.sqlite"
    with TrackDB(db_path) as db:
        _record(db, 1, "A", 10, 3)
        _record(db, 1, "A", 50, 0)
    assert calculate_track_growth(db_path, days=-1) == []


def test_track_growth_sorts_secondary_by_delta(tmp_path):
    db_path = tmp_path / "t.sqlite"
    with TrackDB(db_path) as db:
        # gleiche Wachstumsrate (1.0), unterschiedliche Deltas
        _record(db, 1, "Klein", 10, 3)
        _record(db, 1, "Klein", 20, 0)
        _record(db, 2, "Groß", 100, 3)
        _record(db, 2, "Groß", 200, 0)
    growth = calculate_track_growth(db_path, days=7, min_initial_likes=10)
    assert [g.artist for g in growth] == ["Groß", "Klein"]


def test_track_growth_limit(tmp_path):
    db_path = tmp_path / "t.sqlite"
    with TrackDB(db_path) as db:
        for i, rate in enumerate([1, 2, 3], start=1):
            _record(db, i, f"A{i}", 10, 3)
            _record(db, i, f"A{i}", 10 * (rate + 1), 0)
    growth = calculate_track_growth(db_path, days=7, min_initial_likes=10, limit=2)
    assert len(growth) == 2
    assert [g.sc_id for g in growth] == [3, 2]


def test_missing_table_returns_empty(tmp_path):
    db_path = tmp_path / "notracks.sqlite"
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE other (x INTEGER)")
    con.commit()
    con.close()
    assert calculate_track_growth(db_path, days=7) == []
    assert calculate_artist_trends(db_path, days=7) == []


# ---------------- Artist-Aggregation ----------------
def test_artist_trends_aggregates_and_filters(tmp_path):
    db_path = tmp_path / "t.sqlite"
    with TrackDB(db_path) as db:
        # Artist "Groß": zwei Tracks, Startsumme 100, Delta 100
        _record(db, 1, "Groß", 50, 5)
        _record(db, 1, "Groß", 100, 0)
        _record(db, 2, "Groß", 50, 5)
        _record(db, 2, "Groß", 100, 0)
        # Artist "Klein": Startsumme 5 unter min_initial_likes=20
        _record(db, 3, "Klein", 5, 5)
        _record(db, 3, "Klein", 50, 0)
    trends = calculate_artist_trends(db_path, days=7, min_initial_likes=20)
    assert len(trends) == 1
    assert trends[0].artist == "Groß"
    assert trends[0].track_count == 2
    assert trends[0].end_likes == 200
    assert abs(trends[0].growth_rate - 1.0) < 1e-9


def test_artist_trends_limit(tmp_path):
    db_path = tmp_path / "t.sqlite"
    with TrackDB(db_path) as db:
        for i, rate in enumerate([1, 2, 3], start=1):
            _record(db, i, f"A{i}", 100, 3)
            _record(db, i, f"A{i}", 100 * (rate + 1), 0)
    trends = calculate_artist_trends(db_path, days=7, min_initial_likes=20, limit=2)
    assert [t.artist for t in trends] == ["A3", "A2"]


# ---------------- Formatierung ----------------
def test_format_trend_report_empty():
    assert "Keine Trends" in format_trend_report([], [], days=7)


def test_format_trend_report_includes_all_sections():
    artists = [ArtistGrowth("Svetec", 1, 50, 150, 100, 2.0)]
    tracks = [TrackGrowth(1, "Svetec", "Accelerated", 50, 150, 100, 2.0)]
    report = format_trend_report(artists, tracks, days=7)
    assert "Trend-Radar (7 Tage)" in report
    assert "Trending Artists" in report
    assert "Top-Tracks" in report
    assert "Svetec" in report
    assert "Accelerated" in report
    assert "+200%" in report
