"""Akzeptanztests: Bot-Befehl /mix – harmonisch passende Tracks aus der Track-DB."""
from pathlib import Path
from unittest.mock import patch

import pytest

from sc_digger import bot as b
from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def cfg(tmp_path):
    c = Config.load(ROOT / "config.yaml")
    c.raw["state"] = {**c["state"], "track_db_path": str(tmp_path / "t.sqlite"),
                      "db_path": str(tmp_path / "s.sqlite")}
    with TrackDB(c["state"]["track_db_path"]) as db:
        for i, (key, bpm, artist) in enumerate([("5A", 155.0, "Artist One"), ("6A", 156.5, "A & B"),
                                                 ("9B", 155.0, "Wrong Key"), ("5A", 170.0, "Too Fast")]):
            db.upsert_track(TrackRecord(path=f"/m/{i}.wav", mtime=1.0, size=1, key_camelot=key,
                                        bpm=bpm, artist=artist, title=f"Tune {i}",
                                        status=TrackStatus.ARCHIVE))
    return c


def test_mix_reply_lists_compatible_tracks(cfg):
    text = b.mix_reply(cfg, "/mix 5A 155")
    lines = text.split("\n")
    assert lines[0] == "🎛 5A · 155 BPM ±3: 2 Tracks"
    assert lines[1] == "Artist One – Tune 0 · 155.0 BPM · 5A"
    assert "Wrong Key" not in text and "Too Fast" not in text


def test_mix_reply_escapes_html(cfg):
    assert "A &amp; B – Tune 1" in b.mix_reply(cfg, "/mix 5a 155")


def test_mix_reply_custom_tolerance_and_decimal_comma(cfg):
    text = b.mix_reply(cfg, "/mix 5A 157,5 1")
    assert text.split("\n")[0] == "🎛 5A · 157.5 BPM ±1: 1 Track"


def test_mix_reply_bot_suffix_is_ignored(cfg):
    assert b.mix_reply(cfg, "/mix@sc_digger_bot 5A 155").startswith("🎛 5A")


@pytest.mark.parametrize("text", ["/mix", "/mix 5A", "/mix 13A 155", "/mix 5A schnell",
                                  "/mix 5A 30", "/mix 5A 155 0", "/mix 5A 155 25"])
def test_mix_reply_invalid_input_returns_usage(cfg, text):
    assert b.mix_reply(cfg, text) == b.MIX_USAGE


def test_handle_message_sends_mix_reply(cfg):
    sent = []
    with patch.object(b, "_send_text", side_effect=lambda c, chat, msg: sent.append((chat, msg))):
        b.handle_message(cfg, None, "42", "/mix 5A 155")
    assert sent and sent[0][0] == "42" and sent[0][1].startswith("🎛 5A")


def test_handle_message_reports_failure(cfg):
    sent = []
    with patch.object(b, "mix_reply", side_effect=RuntimeError("DB kaputt")), \
         patch.object(b, "_send_text", side_effect=lambda c, chat, msg: sent.append(msg)):
        b.handle_message(cfg, None, "42", "/mix 5A 155")
    assert sent == ["Mix-Suche fehlgeschlagen, siehe Container-Log."]


def test_help_mentions_mix():
    assert "/mix" in b.HELP_TEXT
