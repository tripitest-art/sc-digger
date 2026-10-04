"""Eigene Tests zu Tag-Trends und Plays-Wachstum (Issue #181, Teil von #54).

Ergänzt die Akzeptanztests um `limit`, das `days`-Fenster, Tracks mit nur einem
Snapshot im Fenster und Tags mit Komma.
"""
from __future__ import annotations

from sc_digger.db import TrackDB
from sc_digger.trends import (
    ArtistGrowth,
    TagTrend,
    TrackGrowth,
    calculate_artist_trends,
    calculate_tag_trends,
    calculate_track_growth,
    format_trend_report,
)


def _snap(db, sc_id, artist, tags, at, likes, plays=0, reposts=0, comments=0):
    db.record_track_snapshot(sc_id=sc_id, artist=artist, title=f"T{sc_id}", plays=plays,
                             likes=likes, reposts=reposts, comments=comments,
                             recorded_at=at, tags=tags)


def test_tag_trends_respects_limit(tmp_path):
    path = tmp_path / "t.sqlite"
    with TrackDB(path) as db:
        _snap(db, 1, "A", ["alpha"], "2026-10-01 10:00:00", likes=10)
        _snap(db, 2, "B", ["alpha"], "2026-10-01 10:00:00", likes=10)
        _snap(db, 1, "A", ["alpha"], "2026-10-03 10:00:00", likes=40)
        _snap(db, 2, "B", ["alpha"], "2026-10-03 10:00:00", likes=40)
        _snap(db, 3, "C", ["beta"], "2026-10-01 10:00:00", likes=10)
        _snap(db, 4, "D", ["beta"], "2026-10-01 10:00:00", likes=10)
        _snap(db, 3, "C", ["beta"], "2026-10-03 10:00:00", likes=100)
        _snap(db, 4, "D", ["beta"], "2026-10-03 10:00:00", likes=100)
    all_trends = calculate_tag_trends(path, days=7)
    assert [t.tag for t in all_trends] == ["beta", "alpha"]
    assert [t.tag for t in calculate_tag_trends(path, days=7, limit=1)] == ["beta"]


def test_tag_trends_respects_day_window(tmp_path):
    """Snapshots außerhalb des Fensters zählen weder als Start noch als Ende."""
    path = tmp_path / "t.sqlite"
    with TrackDB(path) as db:
        # Alter Snapshot deutlich vor dem Fenster (jüngster = 2026-10-10).
        _snap(db, 1, "A", ["old"], "2026-09-01 10:00:00", likes=10)
        _snap(db, 2, "B", ["old"], "2026-09-01 10:00:00", likes=10)
        # Im Fenster (7 Tage) liegt nur je ein Snapshot -> kein Wachstum.
        _snap(db, 1, "A", ["new"], "2026-10-10 10:00:00", likes=90)
        _snap(db, 2, "B", ["new"], "2026-10-10 10:00:00", likes=90)
    assert calculate_tag_trends(path, days=7, min_tracks=1) == []


def test_tag_trends_single_snapshot_track_has_no_growth(tmp_path):
    """Ein Track mit nur einem Snapshot im Fenster hat delta 0 und zählt nicht."""
    path = tmp_path / "t.sqlite"
    with TrackDB(path) as db:
        _snap(db, 1, "A", ["solo"], "2026-10-03 10:00:00", likes=90)
    assert calculate_tag_trends(path, days=7, min_tracks=1) == []


def test_tag_trends_tag_with_comma_in_name(tmp_path):
    """Tags werden als JSON gespeichert; ein Komma im Namen bleibt ein Tag."""
    path = tmp_path / "t.sqlite"
    with TrackDB(path) as db:
        _snap(db, 1, "A", ["dark, hard"], "2026-10-01 10:00:00", likes=10)
        _snap(db, 2, "B", ["dark, hard"], "2026-10-01 10:00:00", likes=10)
        _snap(db, 1, "A", ["dark, hard"], "2026-10-03 10:00:00", likes=40)
        _snap(db, 2, "B", ["dark, hard"], "2026-10-03 10:00:00", likes=40)
    trends = calculate_tag_trends(path, days=7, min_tracks=2)
    assert [t.tag for t in trends] == ["dark, hard"]
    assert trends[0].track_count == 2


def test_delta_plays_absent_columns_are_zero(tmp_path):
    """Tabellen ohne `plays`/`tags` liefern delta_plays 0 statt Fehler."""
    import sqlite3

    path = tmp_path / "alt.sqlite"
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE track_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, sc_id INTEGER, "
        "artist TEXT, title TEXT, likes INTEGER, recorded_at TEXT)"
    )
    con.execute("INSERT INTO track_snapshots (sc_id, artist, likes, recorded_at) "
                "VALUES (1, 'A', 10, '2026-10-01 10:00:00')")
    con.execute("INSERT INTO track_snapshots (sc_id, artist, likes, recorded_at) "
                "VALUES (1, 'A', 50, '2026-10-03 10:00:00')")
    con.commit()
    con.close()
    (g,) = calculate_track_growth(path, days=7, min_initial_likes=10)
    assert g.delta_plays == 0
    (a,) = calculate_artist_trends(path, days=7, min_initial_likes=10)
    assert a.delta_plays == 0
    assert calculate_tag_trends(path, days=7, min_tracks=1) == []
