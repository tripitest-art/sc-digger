from pathlib import Path
import sqlite3
from sc_digger.web.database import save_track_feedback

def test_save_track_feedback_valid(tmp_path: Path):
    track_db = tmp_path / "tracks.sqlite"
    with sqlite3.connect(track_db) as db:
        db.execute(
            "CREATE TABLE sc_feedback (sc_id INTEGER PRIMARY KEY, url TEXT, value TEXT, updated_at TEXT)"
        )

    ok = save_track_feedback(track_db, 12345, "like")
    assert ok is True

    with sqlite3.connect(track_db) as db:
        row = db.execute("SELECT value FROM sc_feedback WHERE sc_id=12345").fetchone()
        assert row is not None
        assert row[0] == "like"

def test_save_track_feedback_invalid(tmp_path: Path):
    track_db = tmp_path / "tracks.sqlite"
    ok = save_track_feedback(track_db, 12345, "invalid_choice")
    assert ok is False
