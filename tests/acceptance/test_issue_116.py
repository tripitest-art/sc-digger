"""Akzeptanztests: Sonntags-Digest & optionale Trend-Grafik (Issue #116, Teil 2 von #55)."""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import sc_digger.stats as stats_mod
from sc_digger.main import is_sunday
from sc_digger.models import Config
from sc_digger.output import TelegramError, send_telegram_photo
from sc_digger.stats import DigestStats, render_stats_chart, send_weekly_digest

ROOT = Path(__file__).resolve().parents[2]
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def make_cfg(tmp_path, **digest):
    """Eigene Config-Kopie mit Datenbanken in tmp_path (nie die echten Pfade)."""
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    cfg.raw["digest"] = dict(digest)
    return cfg


def make_state_db(tmp_path):
    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        db.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, mode TEXT, finished_at TEXT, ok INTEGER, found INTEGER, error TEXT)")
        db.execute("INSERT INTO runs (mode, finished_at, ok, found) VALUES ('discover', CURRENT_TIMESTAMP, 1, 120)")
        db.execute("INSERT INTO runs (mode, finished_at, ok, found) VALUES ('discover', CURRENT_TIMESTAMP, 1, 80)")


@pytest.fixture
def telegram_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "4711")


@pytest.fixture
def calls(monkeypatch):
    """Ersetzt telegram_call in output.py und sammelt (Token, Methode, Parameter)."""
    sent = []

    def fake_call(token, method, **kw):
        sent.append((token, method, kw))
        return {"ok": True}

    monkeypatch.setattr("sc_digger.output.telegram_call", fake_call)
    return sent


# ------------------------------------------------------------------ is_sunday
def test_is_sunday_with_fixed_datetimes():
    tz = ZoneInfo("Europe/Berlin")
    assert is_sunday(datetime(2026, 10, 4, 12, 0, tzinfo=tz)) is True    # Sonntag
    assert is_sunday(datetime(2026, 10, 5, 12, 0, tzinfo=tz)) is False   # Montag
    assert is_sunday(datetime(2026, 10, 3, 23, 59, tzinfo=tz)) is False  # Samstag


# ------------------------------------------------------------------ render_stats_chart
def test_render_stats_chart_returns_png():
    stats = DigestStats(days=7, runs_total=7, runs_ok=7, tracks_scanned=250,
                        tracks_inbox=15, tracks_rejected=4, likes=5, dislikes=1)
    png = render_stats_chart(stats)
    assert isinstance(png, bytes)
    assert png.startswith(PNG_MAGIC)


def test_render_stats_chart_without_data_returns_none():
    assert render_stats_chart(DigestStats(days=7, runs_total=0, tracks_scanned=0)) is None


def test_render_stats_chart_without_matplotlib_returns_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "matplotlib", None)
    monkeypatch.setitem(sys.modules, "matplotlib.pyplot", None)
    assert render_stats_chart(DigestStats(days=7, runs_total=5, tracks_scanned=50)) is None


# ------------------------------------------------------------------ send_telegram_photo
def test_send_telegram_photo_calls_send_photo(telegram_env, calls):
    send_telegram_photo(Config({}), b"PNGDATA", "Unterschrift <b>fett</b>", chat_id="999", filename="woche.png")
    assert len(calls) == 1
    token, method, kw = calls[0]
    assert token == "tok123"
    assert method == "sendPhoto"
    assert kw["data"]["chat_id"] == "999"
    assert kw["data"]["caption"] == "Unterschrift <b>fett</b>"
    assert kw["data"]["parse_mode"] == "HTML"
    assert kw["files"]["photo"] == ("woche.png", b"PNGDATA", "image/png")


def test_send_telegram_photo_uses_default_chat_and_filename(telegram_env, calls):
    send_telegram_photo(Config({}), b"PNGDATA", "x")
    _, method, kw = calls[0]
    assert method == "sendPhoto"
    assert kw["data"]["chat_id"] == "4711"
    assert kw["files"]["photo"][0] == "stats.png"


def test_send_telegram_photo_without_bytes_sends_text_only(telegram_env, calls):
    send_telegram_photo(Config({}), None, "Nur Text")
    assert [m for _, m, _ in calls] == ["sendMessage"]
    payload = calls[0][2]["json"]
    assert payload["chat_id"] == "4711"
    assert payload["text"] == "Nur Text"
    assert payload["parse_mode"] == "HTML"


def test_send_telegram_photo_truncates_long_caption(telegram_env, calls):
    long_text = "A" * 1500
    send_telegram_photo(Config({}), b"PNGDATA", long_text)
    assert [m for _, m, _ in calls] == ["sendPhoto", "sendMessage"]
    caption = calls[0][2]["data"]["caption"]
    assert 0 < len(caption) <= 1024
    assert calls[1][2]["json"]["text"] == long_text


def test_send_telegram_photo_error_falls_back_to_text(telegram_env, monkeypatch):
    sent = []

    def fake_call(token, method, **kw):
        sent.append(method)
        if method == "sendPhoto":
            raise TelegramError("sendPhoto: Telegram 400: kaputt")
        return {"ok": True}

    monkeypatch.setattr("sc_digger.output.telegram_call", fake_call)
    send_telegram_photo(Config({}), b"PNGDATA", "Fallback-Text")  # darf nicht werfen
    assert sent == ["sendPhoto", "sendMessage"]


def test_send_telegram_photo_without_credentials_makes_no_call(monkeypatch, calls):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    send_telegram_photo(Config({}), b"PNGDATA", "x")
    assert calls == []


# ------------------------------------------------------------------ send_weekly_digest
def test_weekly_digest_dry_run_prints_and_sends_nothing(tmp_path, monkeypatch, capsys):
    make_state_db(tmp_path)
    cfg = make_cfg(tmp_path)
    called = []
    monkeypatch.setattr(stats_mod, "send_telegram_photo", lambda *a, **k: called.append((a, k)))
    result = send_weekly_digest(cfg, dry_run=True)
    assert isinstance(result, DigestStats)
    assert result.runs_total == 2
    assert result.tracks_scanned == 200
    assert "Woche im Überblick" in capsys.readouterr().out
    assert called == []


def test_weekly_digest_no_telegram_sends_nothing(tmp_path, monkeypatch, capsys):
    make_state_db(tmp_path)
    cfg = make_cfg(tmp_path)
    called = []
    monkeypatch.setattr(stats_mod, "send_telegram_photo", lambda *a, **k: called.append((a, k)))
    send_weekly_digest(cfg, no_telegram=True, chart=True)
    assert "Woche im Überblick" in capsys.readouterr().out
    assert called == []


def test_weekly_digest_with_chart_sends_photo(tmp_path, monkeypatch):
    make_state_db(tmp_path)
    cfg = make_cfg(tmp_path)
    called = []

    def fake_photo(cfg_, photo_bytes, caption=None, **kw):
        called.append((photo_bytes, caption, kw))

    monkeypatch.setattr(stats_mod, "send_telegram_photo", fake_photo)
    send_weekly_digest(cfg, chart=True, chat_id="999")
    assert len(called) == 1
    photo_bytes, caption, kw = called[0]
    assert photo_bytes.startswith(PNG_MAGIC)
    assert "Woche im Überblick" in caption
    assert kw.get("chat_id") == "999"


def test_weekly_digest_without_chart_does_not_render(tmp_path, monkeypatch):
    make_state_db(tmp_path)
    cfg = make_cfg(tmp_path, stats_chart=True)  # Parameter chart=False schlägt die Config
    called = []
    monkeypatch.setattr(stats_mod, "send_telegram_photo",
                        lambda cfg_, photo_bytes, caption=None, **kw: called.append((photo_bytes, caption)))

    def no_render(stats):
        raise AssertionError("render_stats_chart darf nicht aufgerufen werden")

    monkeypatch.setattr(stats_mod, "render_stats_chart", no_render)
    send_weekly_digest(cfg, chart=False)
    assert len(called) == 1
    assert called[0][0] is None
    assert "Woche im Überblick" in called[0][1]


def test_weekly_digest_chart_default_comes_from_config(tmp_path, monkeypatch):
    make_state_db(tmp_path)
    rendered = []
    monkeypatch.setattr(stats_mod, "send_telegram_photo", lambda *a, **k: None)
    monkeypatch.setattr(stats_mod, "render_stats_chart", lambda s: rendered.append(s) or b"X")
    send_weekly_digest(make_cfg(tmp_path))                    # nicht gesetzt: aus
    assert rendered == []
    send_weekly_digest(make_cfg(tmp_path, stats_chart=True))  # an
    assert len(rendered) == 1


def test_weekly_digest_telegram_error_does_not_raise(tmp_path, monkeypatch):
    make_state_db(tmp_path)

    def boom(*a, **k):
        raise TelegramError("sendMessage: Telegram 500: kaputt")

    monkeypatch.setattr(stats_mod, "send_telegram_photo", boom)
    result = send_weekly_digest(make_cfg(tmp_path), chart=False)
    assert isinstance(result, DigestStats)


# ------------------------------------------------------------------ config.yaml
def test_config_defaults_are_off():
    digest = Config.load(ROOT / "config.yaml")["digest"]
    assert digest["sunday_summary"] is False
    assert digest["stats_chart"] is False
