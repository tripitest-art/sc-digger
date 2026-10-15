from pathlib import Path
import sqlite3
from sc_digger.web.database import query_tracks

def test_query_tracks_includes_rb_plays_and_sc_snapshots(tmp_path: Path):
    track_db = tmp_path / "tracks.sqlite"
    with sqlite3.connect(track_db) as db:
        db.execute(
            "CREATE TABLE tracks ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE NOT NULL, mtime REAL NOT NULL, "
            "size INTEGER NOT NULL, artist TEXT, title TEXT, format TEXT, bitrate_kbps REAL, cutoff_hz INTEGER, "
            "quality_status TEXT NOT NULL DEFAULT 'unknown', bpm REAL, key_camelot TEXT, key_name TEXT, "
            "status TEXT NOT NULL DEFAULT 'archive', adopted_at TEXT, feedback TEXT, source_url TEXT, "
            "created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, "
            "rb_play_count INTEGER)"
        )
        db.execute(
            "CREATE TABLE track_snapshots ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, sc_id INTEGER NOT NULL, artist TEXT, title TEXT, "
            "plays INTEGER, likes INTEGER, reposts INTEGER, comments INTEGER, recorded_at TEXT)"
        )
        db.execute(
            "INSERT INTO tracks (id, path, mtime, size, artist, title, rb_play_count) "
            "VALUES (1, '/inbox/150/8A/[999] Artist - Titel.wav', 1.0, 1000, 'Artist', 'Titel', 4)"
        )
        db.execute(
            "INSERT INTO tracks (id, path, mtime, size, artist, title, rb_play_count) "
            "VALUES (2, '/inbox/150/8A/Other Track.wav', 1.0, 1000, 'Other', 'Track', NULL)"
        )
        db.execute(
            "INSERT INTO track_snapshots (sc_id, artist, title, plays, likes, reposts, comments, recorded_at) "
            "VALUES (999, 'Artist', 'Titel', 5600, 420, 30, 8, '2026-10-14 12:00:00')"
        )

    res = query_tracks(track_db)
    assert res.total == 2
    item1 = [i for i in res.items if i["id"] == 1][0]
    item2 = [i for i in res.items if i["id"] == 2][0]

    assert item1["rb_play_count"] == 4
    assert item1["sc_plays"] == 5600
    assert item1["sc_likes"] == 420
    assert item1["sc_reposts"] == 30

    assert item2["rb_play_count"] is None
    assert item2["sc_plays"] is None
