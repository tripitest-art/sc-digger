"""Akzeptanztests: Vorperioden-Vergleich in der Statistik (Teil 3 von #55)."""
import sqlite3
from datetime import datetime

from sc_digger.stats import calculate_stats, format_stats, _format_change

FIXED_NOW = datetime(2026, 10, 6, 12, 0, 0)


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW


def _db_schema(state_db, tracks_db):
    with sqlite3.connect(state_db) as db:
        db.execute(
            "CREATE TABLE runs (id INTEGER PRIMARY KEY, mode TEXT, "
            "finished_at TEXT, ok INTEGER, found INTEGER, error TEXT)"
        )
    with sqlite3.connect(tracks_db) as db:
        db.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT, artist TEXT, "
            "status TEXT, quality_status TEXT, created_at TEXT)"
        )
        db.execute(
            "CREATE TABLE sc_feedback (sc_id INTEGER PRIMARY KEY, value TEXT, updated_at TEXT)"
        )


def _fill(state_db, tracks_db):
    with sqlite3.connect(state_db) as db:
        # runs: Vorperiode ok=1/found=30, aktuell ok=2/found=20
        db.execute("INSERT INTO runs (mode, finished_at, ok, found) VALUES ('discover', '2026-09-25 10:00:00', 1, 20)")
        db.execute("INSERT INTO runs (mode, finished_at, ok, found) VALUES ('discover', '2026-09-27 10:00:00', 0, 10)")
        db.execute("INSERT INTO runs (mode, finished_at, ok, found) VALUES ('discover', '2026-10-01 10:00:00', 1, 15)")
        db.execute("INSERT INTO runs (mode, finished_at, ok, found) VALUES ('discover', '2026-10-03 10:00:00', 1, 5)")
    with sqlite3.connect(tracks_db) as db:
        # tracks: Vorperiode 1 inbox + 1 rejected, aktuell 3 inbox + 0 rejected
        db.execute("INSERT INTO tracks (status, created_at) VALUES ('inbox', '2026-09-25 10:00:00')")
        db.execute("INSERT INTO tracks (status, created_at) VALUES ('rejected', '2026-09-26 10:00:00')")
        db.execute("INSERT INTO tracks (status, created_at) VALUES ('inbox', '2026-10-01 10:00:00')")
        db.execute("INSERT INTO tracks (status, created_at) VALUES ('inbox', '2026-10-02 10:00:00')")
        db.execute("INSERT INTO tracks (status, created_at) VALUES ('inbox', '2026-10-03 10:00:00')")
        # sc_feedback: Vorperiode 2 likes, aktuell 5 likes
        for i, ts in enumerate(
            ["2026-09-25 10:00:00", "2026-09-26 10:00:00",
             "2026-10-01 10:00:00", "2026-10-02 10:00:00",
             "2026-10-03 10:00:00", "2026-10-04 10:00:00",
             "2026-10-05 10:00:00"],
            start=1,
        ):
            db.execute(
                "INSERT INTO sc_feedback (sc_id, value, updated_at) VALUES (?, 'like', ?)",
                (i, ts),
            )


def test_format_change_rules():
    assert _format_change(1, 3) == " (+200 % ggü. Vorperiode)"
    assert _format_change(2, 5) == " (+150 % ggü. Vorperiode)"
    assert _format_change(1, 2) == " (+100 % ggü. Vorperiode)"
    assert _format_change(30, 20) == " (−33 % ggü. Vorperiode)"
    assert _format_change(1, 0) == " (−100 % ggü. Vorperiode)"
    assert _format_change(5, 5) == " (0 % ggü. Vorperiode)"
    assert _format_change(0, 5) == ""


def test_prev_values_are_calculated(tmp_path, monkeypatch):
    monkeypatch.setattr("sc_digger.stats.datetime", FixedDateTime)
    state_db = tmp_path / "state.sqlite"
    tracks_db = tmp_path / "tracks.sqlite"
    _db_schema(state_db, tracks_db)
    _fill(state_db, tracks_db)

    stats = calculate_stats(tracks_db, state_db, days=7)
    assert stats.runs_ok == 2
    assert stats.prev_runs_ok == 1
    assert stats.tracks_scanned == 20
    assert stats.prev_tracks_scanned == 30
    assert stats.tracks_inbox == 3
    assert stats.prev_tracks_inbox == 1
    assert stats.tracks_rejected == 0
    assert stats.prev_tracks_rejected == 1
    assert stats.likes == 5
    assert stats.prev_likes == 2


def test_format_stats_shows_change_on_all_core_metrics(tmp_path, monkeypatch):
    monkeypatch.setattr("sc_digger.stats.datetime", FixedDateTime)
    state_db = tmp_path / "state.sqlite"
    tracks_db = tmp_path / "tracks.sqlite"
    _db_schema(state_db, tracks_db)
    _fill(state_db, tracks_db)

    text = format_stats(calculate_stats(tracks_db, state_db, days=7))
    assert "+100 % ggü. Vorperiode" in text  # Runs ok
    assert "−33 % ggü. Vorperiode" in text   # gescannt
    assert "+200 % ggü. Vorperiode" in text  # Inbox
    assert "−100 % ggü. Vorperiode" in text  # abgelehnt
    assert "+150 % ggü. Vorperiode" in text  # Likes


def test_format_stats_no_change_without_previous_period(tmp_path, monkeypatch):
    monkeypatch.setattr("sc_digger.stats.datetime", FixedDateTime)
    state_db = tmp_path / "state.sqlite"
    tracks_db = tmp_path / "tracks.sqlite"
    _db_schema(state_db, tracks_db)
    with sqlite3.connect(state_db) as db:
        db.execute("INSERT INTO runs (mode, finished_at, ok, found) VALUES ('discover', '2026-10-01 10:00:00', 1, 15)")
    with sqlite3.connect(tracks_db) as db:
        db.execute("INSERT INTO tracks (status, created_at) VALUES ('inbox', '2026-10-01 10:00:00')")
        db.execute("INSERT INTO sc_feedback (sc_id, value, updated_at) VALUES (1, 'like', '2026-10-01 10:00:00')")

    text = format_stats(calculate_stats(tracks_db, state_db, days=7))
    assert "ggü. Vorperiode" not in text
