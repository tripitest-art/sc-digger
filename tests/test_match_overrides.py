"""Tests für die Handkorrekturen des Matchers (Issue #325).

Ergänzt die Akzeptanztests um Randfälle: Notiz mit Leerzeichen, Korrektur ohne
Track-Zeile, ältere Track-DB ohne ``match_overrides`` und den Bot-Namen am Befehl.
"""
import sqlite3

from sc_digger import bot
from sc_digger.bot import duplicate_override_reply, not_duplicate_reply
from sc_digger.collection import MANUAL_DUPLICATE_LABEL, Collection
from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.web.database import query_digest
from tests.test_modes import mk


def _cfg(db_path) -> Config:
    return Config({"state": {"track_db_path": str(db_path)}})


def _note(db_path, sc_id) -> str | None:
    with TrackDB(db_path) as db:
        row = db.db.execute(
            "SELECT note FROM match_overrides WHERE sc_id = ?", (sc_id,)
        ).fetchone()
    return row["note"] if row else None


def _state_db(tmp_path, sc_id: int, title: str):
    state_db = tmp_path / "seen.sqlite"
    with sqlite3.connect(state_db) as db:
        db.execute(
            "CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)"
        )
        db.execute(
            "INSERT INTO seen VALUES (?, 'https://soundcloud.com/x', ?, '2026-10-14 06:00:00')",
            (sc_id, title),
        )
    return state_db


def test_note_may_contain_spaces(tmp_path):
    db_path = tmp_path / "tracks.sqlite"
    msg = duplicate_override_reply(
        _cfg(db_path), "/is_duplicate 9995 Vom Vinyl gerippt, nicht bei SoundCloud"
    )
    assert "9995" in msg
    assert _note(db_path, 9995) == "Vom Vinyl gerippt, nicht bei SoundCloud"


def test_second_call_without_note_clears_the_note(tmp_path):
    db_path = tmp_path / "tracks.sqlite"
    cfg = _cfg(db_path)
    not_duplicate_reply(cfg, "/not_duplicate 7 doch ein anderer Track")
    assert _note(db_path, 7) == "doch ein anderer Track"
    not_duplicate_reply(cfg, "/not_duplicate 7")
    assert _note(db_path, 7) is None


def test_override_for_sc_id_without_track_row(tmp_path):
    folder = tmp_path / "collection"
    folder.mkdir()
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        assert db.get_track_by_id(4242) is None
        db.set_match_override(4242, "already_in_collection")
        assert db.list_match_overrides() == {4242: "already_in_collection"}
    # Die Korrektur wirkt auch ohne Track-Zeile und ohne Datei in der Sammlung.
    forced = Collection(folder, match_overrides={4242: "already_in_collection"})
    unlisted = mk(4242, title="Nirgends vorhanden", user={"username": "Fremd"},
                  duration=210_000, full_duration=210_000)
    assert forced.find_duplicate(unlisted) == MANUAL_DUPLICATE_LABEL


def test_query_digest_without_match_overrides_table(tmp_path):
    # Ältere Track-DB (vor Migration 15): der Digest läuft weiter und nutzt den Namensabgleich.
    state_db = _state_db(tmp_path, 42, "Fremd - Titel")
    track_db = tmp_path / "tracks.sqlite"
    with sqlite3.connect(track_db) as db:
        db.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT, artist TEXT, title TEXT, status TEXT)"
        )
        db.execute(
            "INSERT INTO tracks VALUES (1, '/coll/Fremd - Titel.wav', 'Fremd', 'Titel', 'archive')"
        )

    res = query_digest(state_db, track_db)

    assert res.items[0].in_collection is True


def test_reply_accepts_the_bot_name_on_the_command(tmp_path):
    # Telegram hängt den Bot-Namen an: "/is_duplicate@sc_digger_bot 12".
    db_path = tmp_path / "tracks.sqlite"
    msg = duplicate_override_reply(_cfg(db_path), "/is_duplicate@sc_digger_bot 12 Nur eine Notiz")
    assert "12" in msg
    with TrackDB(db_path) as db:
        assert db.get_match_override(12) == "already_in_collection"


def test_reply_without_track_db_path_writes_nothing(tmp_path):
    cfg = Config({"state": {}})
    assert duplicate_override_reply(cfg, "/is_duplicate 5") == "Track-DB nicht konfiguriert."
    assert not_duplicate_reply(cfg, "/not_duplicate 5") == "Track-DB nicht konfiguriert."


def test_handle_message_reports_an_error_as_telegram_text(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path / "tracks.sqlite")
    sent: list[str] = []
    monkeypatch.setattr(bot, "_send_text", lambda cfg, chat_id, text: sent.append(text))

    def boom(*args, **kwargs):
        raise RuntimeError("DB kaputt")

    monkeypatch.setattr(bot, "duplicate_override_reply", boom)
    bot.handle_message(cfg, None, "1", "/is_duplicate 5")

    assert sent == ["Korrektur fehlgeschlagen, siehe Container-Log."]
