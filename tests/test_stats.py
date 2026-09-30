"""Unit-Tests für sc_digger.stats und den Bot-Befehl /stats (Issue #108)."""
import sqlite3
from pathlib import Path

import pytest

from sc_digger.bot import STATS_USAGE, stats_reply
from sc_digger.models import Config
from sc_digger.stats import DigestStats, calculate_stats, format_stats

ROOT = Path(__file__).resolve().parents[1]
CFG = Config.load(ROOT / "config.yaml")


def test_empty_runs_and_tracks(tmp_path):
    state_db = tmp_path / "state.sqlite"
    tracks_db = tmp_path / "tracks.sqlite"

    with sqlite3.connect(state_db) as db:
        db.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, mode TEXT, finished_at TEXT, ok INTEGER, found INTEGER, error TEXT)")

    with sqlite3.connect(tracks_db) as db:
        db.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT, artist TEXT, status TEXT, quality_status TEXT, created_at TEXT)")
        db.execute("CREATE TABLE sc_feedback (sc_id INTEGER PRIMARY KEY, value TEXT, updated_at TEXT)")

    stats = calculate_stats(tracks_db, state_db, days=7)
    assert stats.runs_total == 0
    assert stats.runs_ok == 0
    assert stats.tracks_scanned == 0
    assert stats.tracks_inbox == 0
    assert stats.tracks_rejected == 0
    assert stats.likes == 0
    assert stats.dislikes == 0
    assert stats.later == 0
    assert stats.top_artists == []

    text = format_stats(stats)
    assert "0/0" in text
    assert "0" in text
    assert "Top-Artisten" not in text
    assert "Qualitätsverteilung" not in text


def test_calculate_stats_ignores_other_modes_and_statuses(tmp_path):
    state_db = tmp_path / "state.sqlite"
    tracks_db = tmp_path / "tracks.sqlite"

    with sqlite3.connect(state_db) as db:
        db.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, mode TEXT, finished_at TEXT, ok INTEGER, found INTEGER, error TEXT)")
        db.execute("INSERT INTO runs (mode, finished_at, ok, found) VALUES ('discover', CURRENT_TIMESTAMP, 1, 42)")
        # Andere Modi dürfen nicht gezählt werden
        db.execute("INSERT INTO runs (mode, finished_at, ok, found) VALUES ('playlist', CURRENT_TIMESTAMP, 1, 10)")
        db.execute("INSERT INTO runs (mode, finished_at, ok, found) VALUES ('station', CURRENT_TIMESTAMP, 1, 15)")
        db.execute("INSERT INTO runs (mode, finished_at, ok, found) VALUES ('unknown_mode', CURRENT_TIMESTAMP, 1, 5)")
        db.execute("INSERT INTO runs (mode, finished_at, ok, found) VALUES (NULL, CURRENT_TIMESTAMP, 1, 5)")

    with sqlite3.connect(tracks_db) as db:
        db.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT, artist TEXT, status TEXT, quality_status TEXT, created_at TEXT)")
        db.execute("CREATE TABLE sc_feedback (sc_id INTEGER PRIMARY KEY, value TEXT, updated_at TEXT)")
        # Andere Statuswerte außer inbox und rejected
        db.execute("INSERT INTO tracks (path, artist, status, quality_status, created_at) VALUES ('/p1', 'Artist A', 'duplicate', 'ok', CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO tracks (path, artist, status, quality_status, created_at) VALUES ('/p2', 'Artist B', 'stream_only', 'ok', CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO tracks (path, artist, status, quality_status, created_at) VALUES ('/p3', 'Artist C', 'ignored', 'ok', CURRENT_TIMESTAMP)")
        # Unbekannte Feedback-Werte
        db.execute("INSERT INTO sc_feedback (sc_id, value, updated_at) VALUES (1, 'love', CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO sc_feedback (sc_id, value, updated_at) VALUES (2, 'hate', CURRENT_TIMESTAMP)")

    stats = calculate_stats(tracks_db, state_db, days=7)
    assert stats.runs_total == 1
    assert stats.runs_ok == 1
    assert stats.tracks_scanned == 42
    assert stats.tracks_inbox == 0
    assert stats.tracks_rejected == 0
    assert stats.likes == 0
    assert stats.dislikes == 0
    assert stats.later == 0


def test_stats_reply_edge_cases(tmp_path):
    cfg_dict = dict(CFG.raw)
    cfg_dict["state"] = {
        "db_path": str(tmp_path / "state.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    cfg = Config(cfg_dict)

    # Gültige Grenzwerte
    assert "1 Tage" in stats_reply(cfg, "/stats 1")
    assert "365 Tage" in stats_reply(cfg, "/stats 365")

    # Ungültige Grenzwerte & Formate
    assert stats_reply(cfg, "/stats 0") == STATS_USAGE
    assert stats_reply(cfg, "/stats 366") == STATS_USAGE
    assert stats_reply(cfg, "/stats -5") == STATS_USAGE
    assert stats_reply(cfg, "/stats 7 extra param") == STATS_USAGE
    assert stats_reply(cfg, "/stats notanumber") == STATS_USAGE

    # Fehlende DB-Konfiguration
    empty_cfg = Config({})
    assert stats_reply(empty_cfg, "/stats") == "Statistikdaten nicht konfiguriert."
