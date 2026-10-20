"""Akzeptanztests: Korrektur-Rückmeldung für Matcher-Fehler (Ist schon da / Doch kein Duplikat)."""
import sqlite3
from pathlib import Path

import pytest

from sc_digger import bot, main as m
from sc_digger.bot import duplicate_override_reply, not_duplicate_reply
from sc_digger.collection import MANUAL_DUPLICATE_LABEL, Collection, open_collection
from sc_digger.db import MIGRATIONS, TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from sc_digger.web.database import query_digest
from tests.test_modes import mk

ROOT = Path(__file__).resolve().parents[2]


def sc(i, title="Surrender", artist="Ueberrest", seconds=210):
    return mk(i, title=title, user={"username": artist, "permalink_url": "https://x"},
              duration=int(seconds * 1000), full_duration=int(seconds * 1000))


def _seen_db(tmp_path, sc_id, title):
    state_db = tmp_path / "seen.sqlite"
    with sqlite3.connect(state_db) as db:
        db.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        db.execute("INSERT INTO seen VALUES (?, 'u', ?, '2026-10-14 06:00:00')", (sc_id, title))
    return state_db


def _cfg(tmp_path, db_path, collection_dir="gibt-es-nicht"):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["download"] = {**cfg.raw["download"], "collection_dir": str(tmp_path / collection_dir)}
    cfg.raw["state"] = {**cfg.raw["state"], "track_db_path": str(db_path)}
    return cfg


def test_migration_creates_match_overrides_table(tmp_path):
    assert any(name.endswith("match_overrides") for _, name, _ in MIGRATIONS)
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        cols = {r["name"] for r in db.db.execute("PRAGMA table_info(match_overrides)").fetchall()}
        assert {"sc_id", "override_type", "note", "updated_at"} <= cols


def test_db_set_get_list_remove_match_override(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        assert db.get_match_override(12345) is None
        db.set_match_override(12345, "already_in_collection", note="Vom Vinyl gerippt")
        assert db.get_match_override(12345) == "already_in_collection"
        db.set_match_override(12345, "not_duplicate", note="Anderer Track")
        assert db.list_match_overrides() == {12345: "not_duplicate"}
        assert db.remove_match_override(12345) is True
        assert db.remove_match_override(12345) is False
        assert db.get_match_override(12345) is None


def test_db_rejects_unknown_override_type(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        with pytest.raises(ValueError):
            db.set_match_override(1, "vielleicht")
        assert db.list_match_overrides() == {}


def test_collection_respects_match_overrides(tmp_path):
    folder = tmp_path / "collection"
    folder.mkdir()
    (folder / "Ueberrest - Surrender.wav").write_bytes(b"x")
    t = sc(101)
    assert Collection(folder).find_duplicate(t) == "Ueberrest - Surrender.wav"
    assert Collection(folder, match_overrides={101: "not_duplicate"}).find_duplicate(t) is None

    other = sc(102, title="Vollkommen Anderer Titel", artist="Fremd")
    assert Collection(folder).find_duplicate(other) is None
    forced = Collection(folder, match_overrides={102: "already_in_collection"})
    assert forced.find_duplicate(other) == MANUAL_DUPLICATE_LABEL


def test_open_collection_passes_the_overrides_from_the_db(tmp_path):
    folder = tmp_path / "collection"
    folder.mkdir()
    (folder / "Ueberrest - Surrender.wav").write_bytes(b"x")
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.set_match_override(101, "not_duplicate")
    coll = open_collection(_cfg(tmp_path, db_path, "collection"))
    assert coll.find_duplicate(sc(101)) is None
    assert coll.find_duplicate(sc(103)) == "Ueberrest - Surrender.wav"


def test_process_applies_the_overrides_in_the_daily_run(tmp_path):
    folder = tmp_path / "collection"
    folder.mkdir()
    (folder / "Ueberrest - Surrender.wav").write_bytes(b"x")
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.set_match_override(101, "not_duplicate")
        db.set_match_override(102, "already_in_collection")
    tracks = [sc(101), sc(102, title="Vollkommen Anderer Titel", artist="Fremd"), sc(103)]
    fresh, dupes = m.process(tracks, _cfg(tmp_path, db_path, "collection"), dry_run=True)
    assert [t.id for t in fresh] == [101]
    assert sorted(t.id for t in dupes) == [102, 103]


def test_query_digest_respects_both_override_types(tmp_path):
    track_db = tmp_path / "tracks.sqlite"
    with TrackDB(track_db) as db:
        db.set_match_override(777, "already_in_collection")
        db.set_match_override(888, "not_duplicate")
        db.upsert_track(TrackRecord(
            path=(tmp_path / "coll" / "Fremd - Titel 2.wav").as_posix(), mtime=1.0, size=1,
            artist="Fremd", title="Titel 2", status=TrackStatus.ARCHIVE,
        ))
    state_db = _seen_db(tmp_path, 777, "Fremd - Titel 1")
    assert query_digest(state_db, track_db, inbox_dir=tmp_path / "inb").items[0].in_collection is True

    state_db.unlink()
    state_db = _seen_db(tmp_path, 888, "Fremd - Titel 2")  # Namens-Treffer, aber freigegeben
    assert query_digest(state_db, track_db, inbox_dir=tmp_path / "inb").items[0].in_collection is False


def test_bot_replies_store_the_overrides(tmp_path):
    db_path = tmp_path / "tracks.sqlite"
    cfg = Config({"state": {"track_db_path": str(db_path)}})

    msg1 = duplicate_override_reply(cfg, "/is_duplicate 444 vom Vinyl")
    assert "444" in msg1 and "vorhanden" in msg1.lower()
    with TrackDB(db_path) as db:
        assert db.get_match_override(444) == "already_in_collection"

    msg2 = not_duplicate_reply(cfg, "/not_duplicate 444")
    assert "444" in msg2 and "kein duplikat" in msg2.lower()
    with TrackDB(db_path) as db:
        assert db.get_match_override(444) == "not_duplicate"


@pytest.mark.parametrize("text", ["/is_duplicate", "/is_duplicate abc", "/is_duplicate 12x"])
def test_bot_reply_with_bad_argument_returns_usage_and_writes_nothing(tmp_path, text):
    db_path = tmp_path / "tracks.sqlite"
    cfg = Config({"state": {"track_db_path": str(db_path)}})
    assert duplicate_override_reply(cfg, text) == bot.DUPLICATE_USAGE
    assert not_duplicate_reply(cfg, text.replace("is_duplicate", "not_duplicate")) == bot.NOT_DUPLICATE_USAGE
    assert not db_path.exists()


def test_handle_message_routes_the_commands_and_help_lists_them(tmp_path, monkeypatch):
    db_path = tmp_path / "tracks.sqlite"
    cfg = Config({"state": {"track_db_path": str(db_path)}})
    sent = []
    monkeypatch.setattr(bot, "_send_text", lambda cfg, chat_id, text: sent.append(text))
    bot.handle_message(cfg, None, "1", "/is_duplicate 555")
    bot.handle_message(cfg, None, "1", "/not_duplicate 556 Notiz")
    assert "555" in sent[0] and "556" in sent[1]
    with TrackDB(db_path) as db:
        assert db.list_match_overrides() == {555: "already_in_collection", 556: "not_duplicate"}
    assert "/is_duplicate" in bot.HELP_TEXT and "/not_duplicate" in bot.HELP_TEXT
