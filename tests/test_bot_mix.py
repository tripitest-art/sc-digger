"""Eigene Tests für den Bot-Befehl /mix (Harmonic Mixing)."""
from pathlib import Path
from unittest.mock import patch
import pytest

from sc_digger import bot as b
from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def empty_cfg(tmp_path):
    c = Config.load(ROOT / "config.yaml")
    c.raw["state"] = {
        **c["state"],
        "track_db_path": str(tmp_path / "empty_tracks.sqlite"),
        "db_path": str(tmp_path / "empty_sc.sqlite"),
    }
    with TrackDB(c["state"]["track_db_path"]):
        pass  # Erzeugt leere Tabellen
    return c


@pytest.fixture
def populated_cfg(tmp_path):
    c = Config.load(ROOT / "config.yaml")
    c.raw["state"] = {
        **c["state"],
        "track_db_path": str(tmp_path / "tracks.sqlite"),
        "db_path": str(tmp_path / "sc.sqlite"),
    }
    with TrackDB(c["state"]["track_db_path"]) as db:
        # 35 kompatible Tracks für 5A anlegen
        for i in range(35):
            db.upsert_track(
                TrackRecord(
                    path=f"/music/Schranz/Artist_{i}/Track_{i}_long_filename_for_testing_length.wav",
                    mtime=1.0,
                    size=1000,
                    key_camelot="5A",
                    bpm=155.0,
                    artist=f"Sehr Langer Artist Name Nummer {i} Special Edition",
                    title=f"Sehr Langer Schranz Track Titel Nummer {i} Original Mix",
                    status=TrackStatus.ARCHIVE,
                )
            )
    return c


def test_mix_reply_empty_db(empty_cfg):
    reply = b.mix_reply(empty_cfg, "/mix 5A 155")
    assert reply == "🎛 5A · 155 BPM ±3: keine passenden Tracks"


def test_mix_reply_whitespace_and_case(populated_cfg):
    # Groß/klein im Befehl und im Key, Leerzeichen am Rand und zwischen Argumenten
    reply1 = b.mix_reply(populated_cfg, "  /MIX   5a   155   ")
    assert reply1.startswith("🎛 5A · 155 BPM ±3: 30 Tracks")

    # Bot-Suffix und Dezimal-Komma bei BPM und Toleranz
    reply2 = b.mix_reply(populated_cfg, " /mix@testbot   5A   155,0   2,5 ")
    assert reply2.startswith("🎛 5A · 155 BPM ±2.5: 30 Tracks")


def test_mix_reply_message_length_under_telegram_limit(populated_cfg):
    # Höchstens 30 Treffer, Gesamtlänge muss unter dem Telegram-Limit (4096 Zeichen) bleiben
    reply = b.mix_reply(populated_cfg, "/mix 5A 155")
    assert len(reply) < 4096
    assert reply.count("\n") == 30  # 1 Header-Zeile + 30 Track-Zeilen


def test_mix_reply_html_escaping_quote_false(tmp_path):
    c = Config.load(ROOT / "config.yaml")
    c.raw["state"] = {
        **c["state"],
        "track_db_path": str(tmp_path / "quote_tracks.sqlite"),
        "db_path": str(tmp_path / "sc.sqlite"),
    }
    with TrackDB(c["state"]["track_db_path"]) as db:
        db.upsert_track(
            TrackRecord(
                path="/m/1.wav",
                mtime=1.0,
                size=1,
                key_camelot="8B",
                bpm=160.0,
                artist='Artist <One> & "Friends"',
                title="It's a 'Great' Track",
                status=TrackStatus.ARCHIVE,
            )
        )
    reply = b.mix_reply(c, "/mix 8B 160")
    # & und <> müssen escaped sein, Anführungszeichen (quote=False) bleiben
    assert "Artist &lt;One&gt; &amp; \"Friends\" – It's a 'Great' Track" in reply


@pytest.mark.parametrize(
    "invalid_text",
    [
        "",
        "   ",
        "/other 5A 155",
        "/mix",
        "/mix 5A",
        "/mix 5A 155 2 extra",
        "/mix 0A 155",
        "/mix 13A 155",
        "/mix 5C 155",
        "/mix 5A 59.9",
        "/mix 5A 250.1",
        "/mix 5A nan",
        "/mix 5A inf",
        "/mix 5A -150",
        "/mix 5A 155 0",
        "/mix 5A 155 -1",
        "/mix 5A 155 20.1",
        "/mix 5A 155 inf",
    ],
)
def test_mix_reply_invalid_parameters_return_usage(empty_cfg, invalid_text):
    assert b.mix_reply(empty_cfg, invalid_text) == b.MIX_USAGE


def test_mix_reply_bpm_boundary_values(empty_cfg):
    # 60.0 und 250.0 sind gültige Grenzwerte
    assert b.mix_reply(empty_cfg, "/mix 5A 60").startswith("🎛 5A · 60 BPM")
    assert b.mix_reply(empty_cfg, "/mix 5A 250").startswith("🎛 5A · 250 BPM")
    # Toleranz 20.0 ist oberer Grenzwert
    assert b.mix_reply(empty_cfg, "/mix 5A 155 20").startswith("🎛 5A · 155 BPM ±20")


def test_handle_message_mix_case_and_suffix(populated_cfg):
    sent = []
    with patch.object(b, "_send_text", side_effect=lambda c, chat, msg: sent.append((chat, msg))):
        b.handle_message(populated_cfg, None, "123", "/MIX@my_bot 5a 155")
    assert len(sent) == 1
    chat_id, text = sent[0]
    assert chat_id == "123"
    assert text.startswith("🎛 5A · 155 BPM ±3: 30 Tracks")


def test_handle_message_exception_handling(populated_cfg):
    sent = []
    with patch.object(b, "mix_reply", side_effect=Exception("Unbekannter Fehler")), \
         patch.object(b, "_send_text", side_effect=lambda c, chat, msg: sent.append((chat, msg))):
        b.handle_message(populated_cfg, None, "123", "/mix 5A 155")
    assert sent == [("123", "Mix-Suche fehlgeschlagen, siehe Container-Log.")]
