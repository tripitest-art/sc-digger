"""Akzeptanztests: Trend-Radar im Wochen-Digest."""
import sqlite3
from pathlib import Path
from unittest.mock import patch

from sc_digger.models import Config
from sc_digger.stats import send_weekly_digest

ROOT = Path(__file__).resolve().parents[2]


def _make_trend_db(path: Path) -> None:
    """Erzeugt eine track_db mit track_snapshots-Tabelle und synthetischen Daten."""
    db = sqlite3.connect(str(path))
    db.execute("""
        CREATE TABLE IF NOT EXISTS track_snapshots (
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
    db.execute("""
        CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sc_id INTEGER,
            artist TEXT,
            title TEXT,
            status TEXT,
            created_at TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS sc_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            value TEXT,
            updated_at TEXT
        )
    """)
    # Tracks mit steigenden Likes über 8 Tage
    for sc_id, artist, title, base_likes, growth_per_day in [
        (100, "Artist A", "Track 1", 50, 30),
        (200, "Artist B", "Track 2", 200, 10),
    ]:
        for day in range(8):
            likes = base_likes + day * growth_per_day
            db.execute(
                "INSERT INTO track_snapshots (sc_id, artist, title, likes, recorded_at) "
                "VALUES (?, ?, ?, ?, datetime('2026-10-01 08:00:00', '+{} days'))".format(day),
                (sc_id, artist, title, likes),
            )
    db.commit()
    db.close()


def _sent_text(mock_call) -> str:
    """Text aller Telegram-Aufrufe: sendMessage liefert ihn in json['text'], sendPhoto in data['caption']."""
    text = ""
    for call in mock_call.call_args_list:
        payload = call.kwargs.get("json") or call.kwargs.get("data") or {}
        text += payload.get("text", "") + payload.get("caption", "")
    return text


def _make_cfg(tmp_path, trend_db=True):
    """Config-Objekt mit übersteuertem state auf tmp_path-Pfade."""
    cfg = Config.load(ROOT / "config.yaml")
    state_db = tmp_path / "seen.sqlite"
    sdb = sqlite3.connect(str(state_db))
    sdb.execute("CREATE TABLE IF NOT EXISTS runs (mode TEXT, ok INTEGER, found INTEGER, finished_at TEXT)")
    sdb.commit()
    sdb.close()
    track_db = tmp_path / "tracks.sqlite"
    cfg.raw["state"] = {
        "db_path": str(state_db),
        "track_db_path": str(track_db),
    }
    if trend_db:
        _make_trend_db(track_db)
    cfg.raw.setdefault("digest", {})
    return cfg


def test_digest_contains_trend_radar(tmp_path, monkeypatch):
    """Der Wochen-Digest enthält den Trend-Radar-Abschnitt, wenn trend_radar=True."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    cfg = _make_cfg(tmp_path)
    cfg.raw["digest"]["trend_radar"] = True
    cfg.raw["digest"]["trend_radar_days"] = 7
    with patch("sc_digger.output.telegram_call") as mock_call:
        send_weekly_digest(cfg, days=7, no_telegram=False, dry_run=False)
    all_text = _sent_text(mock_call)
    assert "📈 Trending Artists" in all_text
    assert "🔥 Top-Tracks" in all_text
    assert "Artist A" in all_text


def test_trend_radar_disabled(tmp_path, monkeypatch):
    """Mit trend_radar=False erscheint kein Trend-Abschnitt."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    cfg = _make_cfg(tmp_path)
    cfg.raw["digest"]["trend_radar"] = False
    with patch("sc_digger.output.telegram_call") as mock_call:
        send_weekly_digest(cfg, days=7, no_telegram=False, dry_run=False)
    all_text = _sent_text(mock_call)
    assert "📈 Trending Artists" not in all_text
    assert "🔥 Top-Tracks" not in all_text


def test_trend_radar_empty_db_no_error(tmp_path, monkeypatch):
    """Leere DB (keine track_snapshots) wirft keinen Fehler, Digest erscheint normal."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    cfg = _make_cfg(tmp_path, trend_db=False)
    cfg.raw["digest"]["trend_radar"] = True
    cfg.raw["digest"]["trend_radar_days"] = 7
    with patch("sc_digger.output.telegram_call") as mock_call:
        send_weekly_digest(cfg, days=7, no_telegram=False, dry_run=False)
    all_text = _sent_text(mock_call)
    assert "📊 Woche im Überblick" in all_text  # Normaler Digest kommt durch
    assert "📈 Trending Artists" not in all_text  # Kein Trend-Abschnitt


def test_config_defaults_trend_radar_on(tmp_path, monkeypatch):
    """Ohne explizite Config ist trend_radar an (Default True)."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    cfg = _make_cfg(tmp_path)
    # Keine digest-Schlüssel setzen → Defaults greifen
    with patch("sc_digger.output.telegram_call") as mock_call:
        send_weekly_digest(cfg, days=7, no_telegram=False, dry_run=False)
    all_text = _sent_text(mock_call)
    assert "📈 Trending Artists" in all_text
    assert "🔥 Top-Tracks" in all_text
