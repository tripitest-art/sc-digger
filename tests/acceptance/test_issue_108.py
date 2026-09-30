"""Akzeptanztests: Digest-Statistiken & /stats-Befehl im Bot (Issue #55)."""
import sqlite3
from pathlib import Path

import pytest

from sc_digger.bot import handle_message, stats_reply
from sc_digger.models import Config
from sc_digger.stats import DigestStats, calculate_stats, format_stats

ROOT = Path(__file__).resolve().parents[2]
CFG = Config.load(ROOT / "config.yaml")


def test_calculate_stats_on_missing_or_empty_dbs(tmp_path):
    stats = calculate_stats(tmp_path / "tracks.sqlite", tmp_path / "state.sqlite", days=7)
    assert isinstance(stats, DigestStats)
    assert stats.days == 7
    assert stats.runs_total == 0
    assert stats.tracks_scanned == 0
    assert stats.tracks_inbox == 0
    assert stats.top_artists == []


def test_calculate_stats_aggregates_correctly(tmp_path):
    state_db = tmp_path / "state.sqlite"
    tracks_db = tmp_path / "tracks.sqlite"

    with sqlite3.connect(state_db) as db:
        db.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, mode TEXT, finished_at TEXT, ok INTEGER, found INTEGER, error TEXT)")
        # 3 discover runs: 2 ok, 1 failed, 150 found
        db.execute("INSERT INTO runs (mode, finished_at, ok, found) VALUES ('discover', CURRENT_TIMESTAMP, 1, 100)")
        db.execute("INSERT INTO runs (mode, finished_at, ok, found) VALUES ('discover', CURRENT_TIMESTAMP, 1, 50)")
        db.execute("INSERT INTO runs (mode, finished_at, ok, found, error) VALUES ('discover', CURRENT_TIMESTAMP, 0, 0, 'Timeout')")
        # Run außerhalb des Zeitfensters (10 Tage alt)
        db.execute("INSERT INTO runs (mode, finished_at, ok, found) VALUES ('discover', datetime('now', '-10 days'), 1, 80)")
        # Anderer Modus
        db.execute("INSERT INTO runs (mode, finished_at, ok, found) VALUES ('playlist', CURRENT_TIMESTAMP, 1, 20)")

    with sqlite3.connect(tracks_db) as db:
        db.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT, artist TEXT, status TEXT, quality_status TEXT, created_at TEXT)")
        db.execute("CREATE TABLE sc_feedback (sc_id INTEGER PRIMARY KEY, value TEXT, updated_at TEXT)")
        # Inbox tracks
        db.execute("INSERT INTO tracks (path, artist, status, quality_status, created_at) VALUES ('/inbox/1.wav', 'Svetec', 'inbox', 'ok', CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO tracks (path, artist, status, quality_status, created_at) VALUES ('/inbox/2.wav', 'Svetec', 'inbox', 'ok', CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO tracks (path, artist, status, quality_status, created_at) VALUES ('/inbox/3.wav', 'O.B.I.', 'inbox', 'ok', CURRENT_TIMESTAMP)")
        # Rejected track
        db.execute("INSERT INTO tracks (path, artist, status, quality_status, created_at) VALUES ('/rej/4.wav', 'Fake', 'rejected', 'fake_transcode', CURRENT_TIMESTAMP)")
        # Feedback
        db.execute("INSERT INTO sc_feedback (sc_id, value, updated_at) VALUES (1, 'like', CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO sc_feedback (sc_id, value, updated_at) VALUES (2, 'like', CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO sc_feedback (sc_id, value, updated_at) VALUES (3, 'dislike', CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO sc_feedback (sc_id, value, updated_at) VALUES (4, 'later', CURRENT_TIMESTAMP)")

    stats = calculate_stats(tracks_db, state_db, days=7)
    assert stats.runs_total == 3
    assert stats.runs_ok == 2
    assert stats.tracks_scanned == 150
    assert stats.tracks_inbox == 3
    assert stats.tracks_rejected == 1
    assert stats.quality_breakdown == {"fake_transcode": 1}
    assert stats.likes == 2
    assert stats.dislikes == 1
    assert stats.later == 1
    assert stats.top_artists == [("Svetec", 2), ("O.B.I.", 1)]


def test_format_stats_text():
    stats = DigestStats(
        days=7,
        runs_total=7,
        runs_ok=7,
        tracks_scanned=1420,
        tracks_inbox=24,
        tracks_rejected=6,
        quality_breakdown={"fake_transcode": 4, "clipped": 2},
        likes=14,
        dislikes=2,
        later=1,
        top_artists=[("Svetec", 3), ("O.B.I.", 2)],
    )
    text = format_stats(stats)
    assert "7 Tage" in text
    assert "1.420" in text or "1420" in text
    assert "24" in text
    assert "fake_transcode: 4" in text
    assert "14 👍" in text
    assert "Svetec (3)" in text


def test_stats_reply_validation_and_parsing(tmp_path):
    cfg_dict = dict(CFG.raw)
    cfg_dict["state"] = {
        "db_path": str(tmp_path / "state.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    cfg = Config(cfg_dict)

    # Standardaufruf (7 Tage)
    res = stats_reply(cfg, "/stats")
    assert "7 Tage" in res

    # Mit expliziter Tagesanzahl
    res30 = stats_reply(cfg, "/stats 30")
    assert "30 Tage" in res30

    # Ungültige Eingaben
    assert "Aufruf: /stats" in stats_reply(cfg, "/stats abc")
    assert "Aufruf: /stats" in stats_reply(cfg, "/stats 0")
    assert "Aufruf: /stats" in stats_reply(cfg, "/stats 500")
    assert "Aufruf: /stats" in stats_reply(cfg, "/stats 7 extra")


def test_bot_message_handler_routes_stats(tmp_path, monkeypatch):
    cfg_dict = dict(CFG.raw)
    cfg_dict["state"] = {
        "db_path": str(tmp_path / "state.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    cfg = Config(cfg_dict)

    sent = []
    monkeypatch.setattr("sc_digger.bot._send_text", lambda c, chat_id, msg: sent.append((chat_id, msg)))

    handle_message(cfg, None, "12345", "/stats")
    assert len(sent) == 1
    assert sent[0][0] == "12345"
    assert "7 Tage" in sent[0][1]
