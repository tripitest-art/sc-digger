"""Eigene Tests für „Mehr davon“ im Telegram-Bot per SoundCloud-URL (Issue #272).

Kein echter Netzwerkzugriff: Audio-Beschaffung und Fingerprint sind durch Fakes ersetzt
(bzw. die Standard-Helfer per monkeypatch), die Track-DB ist eine temporäre SQLite-Datei.
"""
from pathlib import Path

import pytest

from sc_digger import bot, similarity
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.fingerprint import encode_fingerprint
from sc_digger.models import Config

URL = "https://soundcloud.com/a/t1"
WORDS = 200


def _near(values: list[int]) -> list[int]:
    """Kopie mit jedem fünften Bit gekippt: BER exakt 0.20 (20 % der Bits)."""
    out = list(values)
    for pos in range(0, len(values) * 32, 5):
        word, bit = divmod(pos, 32)
        out[word] ^= 1 << bit
    return out


def _fingerprinted(db: TrackDB, tmp_path: Path, artist: str, title: str, values: list[int]) -> TrackRecord:
    p = tmp_path / f"{artist} - {title}.wav"
    p.write_bytes(b"fake-audio")
    rec = db.upsert_track(
        TrackRecord(
            path=str(p), mtime=1.0, size=10, artist=artist, title=title,
            bpm=140, key_camelot="5A",
        )
    )
    db.set_fingerprint(rec.id, encode_fingerprint(values), 60.0)
    return rec


def _fake_fetch(url: str, dest: Path) -> Path:
    dest.write_bytes(b"fake-audio")
    return dest


def test_similar_reply_from_url_lists_similar_track(tmp_path):
    """Der Link-Weg liefert dieselbe Antwort wie eine gesendete Datei: Top-Treffer mit Ähnlichkeit."""
    with TrackDB(tmp_path / "t.sqlite") as db:
        _fingerprinted(db, tmp_path, "Svetec", "Raw", _near([0] * WORDS))
        reply = bot.similar_reply_from_url(
            None, db, URL, fetch_audio=_fake_fetch, compute_fp=lambda path: [0] * WORDS
        )
    assert reply.splitlines()[0] == "🎧 Ähnliche Aufnahmen:"
    assert reply.splitlines()[1] == "1. Svetec – Raw · 140.0 BPM · 5A · Ähnlichkeit 80.0%"


def test_similar_reply_from_url_missing_fingerprint_returns_standard_text(tmp_path):
    """Ohne Fingerprint (fpcalc-Fehler) gibt es den Standard-Fehlertext statt einer Ausnahme."""
    with TrackDB(tmp_path / "t.sqlite") as db:
        reply = bot.similar_reply_from_url(
            None, db, URL, fetch_audio=_fake_fetch, compute_fp=lambda path: None
        )
    assert reply == similarity.SIMILAR_FAILED


def test_similar_reply_from_url_removes_temp_file_on_success(tmp_path):
    seen = {}

    def fetch(url, dest):
        dest.write_bytes(b"fake-audio")
        seen["path"] = dest
        return dest

    with TrackDB(tmp_path / "t.sqlite") as db:
        bot.similar_reply_from_url(
            None, db, URL, fetch_audio=fetch, compute_fp=lambda path: [0] * WORDS
        )
    assert not seen["path"].exists()


def test_similar_reply_from_url_removes_temp_file_on_error(tmp_path):
    """Auch wenn der Download scheitert, bleibt keine temporäre Datei liegen."""
    seen = {}

    def boom(url, dest):
        seen["path"] = dest
        raise RuntimeError("kaputt")

    with TrackDB(tmp_path / "t.sqlite") as db:
        reply = bot.similar_reply_from_url(None, db, URL, fetch_audio=boom)
    assert reply == similarity.SIMILAR_FAILED
    assert not seen["path"].exists()


@pytest.fixture
def cfg(tmp_path):
    return Config({"state": {"track_db_path": str(tmp_path / "tracks.sqlite")}})


@pytest.fixture
def sent(monkeypatch):
    """Fängt die Bot-Antworten ab; Netzwerk- und Fingerprint-Helfer sind gefakt."""
    out = []
    monkeypatch.setattr(bot, "_send_text", lambda c, chat_id, msg: out.append(msg))
    monkeypatch.setattr(bot, "_fetch_audio_via_soundcloud", _fake_fetch)
    monkeypatch.setattr(bot, "_compute_fingerprint_values", lambda path: [0] * WORDS)
    return out


def test_handle_message_routes_similar_url(cfg, sent):
    """'/similar <Link>' läuft über die Ähnlichkeitssuche, nicht über den Playlist-Check."""
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        _fingerprinted(db, Path(cfg["state"]["track_db_path"]).parent, "Svetec", "Raw", _near([0] * WORDS))

    bot.handle_message(cfg, None, "42", f"/similar {URL}")

    assert len(sent) == 1
    assert "1. Svetec – Raw" in sent[0]


def test_handle_message_similar_url_without_hit(cfg, sent):
    bot.handle_message(cfg, None, "42", f"/similar {URL}")

    assert sent == ["Keine ähnlichen Aufnahmen gefunden."]


def test_handle_message_similar_without_url_sends_usage(cfg, sent):
    bot.handle_message(cfg, None, "42", "/similar")

    assert sent == [bot.SIMILAR_USAGE]


def test_similar_is_in_help_text():
    assert "/similar" in bot.HELP_TEXT
