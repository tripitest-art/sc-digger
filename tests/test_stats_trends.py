"""Unit-Tests für den Trend-Radar im Wochen-Digest (Issue #162).

Deckt die Ausgabe-Varianten (dry_run/no_telegram), fehlende DB-Dateien und Fehler aus
`trends.py` ab. Netzwerk und Telegram werden gemockt.
"""
import sqlite3
from pathlib import Path
from unittest.mock import patch

from sc_digger.models import Config
from sc_digger.stats import _trend_section, send_weekly_digest

ROOT = Path(__file__).resolve().parents[1]


def _make_trend_db(path: Path) -> None:
    with sqlite3.connect(str(path)) as db:
        db.execute("""
            CREATE TABLE track_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sc_id INTEGER NOT NULL,
                artist TEXT,
                title TEXT,
                likes INTEGER,
                reposts INTEGER,
                plays INTEGER,
                recorded_at TEXT
            )
        """)
        for day in range(8):
            likes = 50 + day * 30
            db.execute(
                "INSERT INTO track_snapshots (sc_id, artist, title, likes, recorded_at) "
                "VALUES (?, ?, ?, ?, datetime('2026-10-01 08:00:00', '+{} days'))".format(day),
                (100, "Artist A", "Track 1", likes),
            )


def _cfg(tmp_path, *, trend_db=True, **digest):
    cfg = Config.load(ROOT / "config.yaml")
    state_db = tmp_path / "seen.sqlite"
    with sqlite3.connect(str(state_db)) as db:
        db.execute("CREATE TABLE runs (mode TEXT, ok INTEGER, found INTEGER, finished_at TEXT)")
    track_db = tmp_path / "tracks.sqlite"
    if trend_db:
        _make_trend_db(track_db)
    cfg.raw["state"] = {"db_path": str(state_db), "track_db_path": str(track_db)}
    cfg.raw["digest"] = dict(cfg.raw.get("digest") or {})
    cfg.raw["digest"].update(digest)
    return cfg


def test_dry_run_prints_trend_text(tmp_path, capsys):
    cfg = _cfg(tmp_path, trend_radar=True, trend_radar_days=7)
    send_weekly_digest(cfg, days=7, dry_run=True)
    out = capsys.readouterr().out
    assert "📈 Trending Artists" in out
    assert "🔥 Top-Tracks" in out


def test_no_telegram_shows_trend_text(tmp_path, capsys):
    cfg = _cfg(tmp_path, trend_radar=True, trend_radar_days=7)
    send_weekly_digest(cfg, days=7, no_telegram=True)
    out = capsys.readouterr().out
    assert "📈 Trending Artists" in out
    assert "🔥 Top-Tracks" in out


def test_no_telegram_and_dry_run_only_stdout(tmp_path, capsys):
    cfg = _cfg(tmp_path, trend_radar=True, trend_radar_days=7)
    with patch("sc_digger.output.telegram_call") as mock_call:
        send_weekly_digest(cfg, days=7, dry_run=True, no_telegram=True)
    out = capsys.readouterr().out
    assert "📈 Trending Artists" in out
    assert mock_call.call_count == 0


def test_missing_track_db_no_error(tmp_path):
    cfg = _cfg(tmp_path, trend_db=False, trend_radar=True)
    assert _trend_section(cfg, days=7) == ""


def test_trends_error_is_logged_not_raised(tmp_path, caplog):
    cfg = _cfg(tmp_path, trend_radar=True, trend_radar_days=7)
    with patch("sc_digger.stats.calculate_artist_trends",
               side_effect=RuntimeError("kaputt")):
        result = _trend_section(cfg, days=7)
    assert result == ""
    assert "Trend-Radar nicht verfügbar" in caplog.text


def test_trend_radar_disabled_returns_empty(tmp_path):
    cfg = _cfg(tmp_path, trend_radar=False)
    assert _trend_section(cfg, days=7) == ""
