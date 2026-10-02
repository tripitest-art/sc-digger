"""Eigene Tests zu #116: Sonntags-Digest, Diagramm und CLI-Subcommand `stats`.

Keine Netzwerkzugriffe, keine Abhängigkeit von der echten Uhrzeit: `is_sunday` wird über
feste `datetime`-Werte oder Monkeypatch geprüft, Telegram-Aufrufe laufen über Fakes.
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import sc_digger.main as m
from sc_digger.models import Config
from sc_digger.output import send_telegram_photo
from sc_digger.stats import DigestStats, render_stats_chart

ROOT = Path(__file__).resolve().parents[1]
CFG = Config.load(ROOT / "config.yaml")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _cfg(tmp_path, **digest):
    cfg = Config(dict(CFG.raw))
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    cfg.raw["digest"] = dict(CFG.raw.get("digest") or {})
    cfg.raw["digest"].update(digest)
    return cfg


def _make_state_db(tmp_path, *, runs=True):
    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        db.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, mode TEXT, finished_at TEXT, "
                   "ok INTEGER, found INTEGER, error TEXT)")
        if runs:
            db.execute("INSERT INTO runs (mode, finished_at, ok, found) "
                       "VALUES ('discover', CURRENT_TIMESTAMP, 1, 120)")


# ------------------------------------------------------------ Diagramm grob
def test_chart_is_valid_png_with_size():
    stats = DigestStats(days=7, runs_total=7, runs_ok=7, tracks_scanned=250,
                        tracks_inbox=15, tracks_rejected=4, likes=5, dislikes=1)
    png = render_stats_chart(stats)
    assert isinstance(png, bytes) and png.startswith(PNG_MAGIC)
    assert png[12:16] == b"IHDR"
    assert int.from_bytes(png[16:20], "big") > 0
    assert int.from_bytes(png[20:24], "big") > 0


def test_chart_without_data_is_none():
    assert render_stats_chart(DigestStats(days=7, runs_total=0, tracks_scanned=0)) is None


# ------------------------------------------------------------ is_sunday
def test_is_sunday_uses_berlin_default_and_weekday():
    tz = ZoneInfo("Europe/Berlin")
    assert m.is_sunday(datetime(2026, 10, 4, tzinfo=tz)) is True
    assert m.is_sunday(datetime(2026, 10, 5, tzinfo=tz)) is False


# ------------------------------------------------------------ send_telegram_photo, leere Bytes
def test_send_telegram_photo_empty_bytes_goes_text_only(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    sent = []
    monkeypatch.setattr("sc_digger.output.telegram_call",
                        lambda token, method, **kw: sent.append(method))
    send_telegram_photo(Config({}), b"", "nur Text")
    assert sent == ["sendMessage"]


# ------------------------------------------------------------ CLI-Subcommand
def test_run_stats_prints_without_send(tmp_path, capsys):
    _make_state_db(tmp_path)
    m.run_stats(_cfg(tmp_path), days=7)
    out = capsys.readouterr().out
    assert "Statistiken der letzten 7 Tage" in out


def test_run_stats_writes_chart_file(tmp_path, capsys):
    _make_state_db(tmp_path)
    chart = tmp_path / "woche.png"
    m.run_stats(_cfg(tmp_path), days=7, chart_path=str(chart))
    assert chart.read_bytes().startswith(PNG_MAGIC)
    assert "Diagramm gespeichert" in capsys.readouterr().out


def test_run_stats_chart_without_data_prints_hint(tmp_path, capsys):
    _make_state_db(tmp_path, runs=False)
    chart = tmp_path / "woche.png"
    m.run_stats(_cfg(tmp_path), days=7, chart_path=str(chart))
    assert not chart.exists()
    assert "Kein Diagramm" in capsys.readouterr().out


def test_run_stats_send_dry_run_prints(tmp_path, capsys):
    _make_state_db(tmp_path)
    m.run_stats(_cfg(tmp_path), send=True, dry_run=True)
    assert "Woche im Überblick" in capsys.readouterr().out


def test_cli_routes_stats_subcommand(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(m, "run_stats", lambda cfg, **kw: captured.update(kw))
    monkeypatch.setattr(sys, "argv", [
        "sc-digger", "stats", "--config", str(ROOT / "config.yaml"),
        "--days", "3", "--chart", str(tmp_path / "c.png"), "--send",
    ])
    m.cli()
    assert captured["days"] == 3
    assert captured["chart_path"] == str(tmp_path / "c.png")
    assert captured["send"] is True


# ------------------------------------------------------------ Haken in _discover
class _DummySC:
    pass


def _stub_discovery(monkeypatch):
    monkeypatch.setattr(m, "SoundCloudClient", lambda *a, **k: _DummySC())
    monkeypatch.setattr(m, "collect_sources",
                        lambda sc, s: m.Discovery([], set(), 1, 1, [], None, None))
    monkeypatch.setattr(m, "process", lambda tracks, cfg, **kw: ([], []))


def test_discover_sends_weekly_digest_on_sunday_when_enabled(tmp_path, monkeypatch):
    _stub_discovery(monkeypatch)
    monkeypatch.setattr(m, "is_sunday", lambda now=None: True)
    called = []
    monkeypatch.setattr(m, "send_weekly_digest", lambda cfg, **kw: called.append(kw))
    m._discover(_cfg(tmp_path, sunday_summary=True), dry_run=True, no_telegram=True)
    assert called == [{"dry_run": True, "no_telegram": True}]


def test_discover_skips_weekly_digest_when_disabled(tmp_path, monkeypatch):
    _stub_discovery(monkeypatch)
    monkeypatch.setattr(m, "is_sunday", lambda now=None: True)
    called = []
    monkeypatch.setattr(m, "send_weekly_digest", lambda cfg, **kw: called.append(kw))
    m._discover(_cfg(tmp_path, sunday_summary=False), dry_run=True, no_telegram=True)
    assert called == []


def test_discover_skips_weekly_digest_on_other_days(tmp_path, monkeypatch):
    _stub_discovery(monkeypatch)
    monkeypatch.setattr(m, "is_sunday", lambda now=None: False)
    called = []
    monkeypatch.setattr(m, "send_weekly_digest", lambda cfg, **kw: called.append(kw))
    m._discover(_cfg(tmp_path, sunday_summary=True), dry_run=True, no_telegram=True)
    assert called == []


def test_discover_digest_failure_does_not_break_run(tmp_path, monkeypatch):
    _stub_discovery(monkeypatch)
    monkeypatch.setattr(m, "is_sunday", lambda now=None: True)

    def boom(cfg, **kw):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(m, "send_weekly_digest", boom)
    assert m._discover(_cfg(tmp_path, sunday_summary=True), dry_run=True, no_telegram=True) == 0
