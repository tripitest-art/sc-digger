"""Akzeptanztests: Feedback speichern und 👍/👎/⏳-Buttons unter den Digest-Nachrichten (Variante A)."""
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sc_digger import main as m
from sc_digger import output as out
from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.soundcloud import SoundCloudClient

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime.now(timezone.utc).isoformat()


def mk(i: int, sc_id: int | None = None):
    return SoundCloudClient._to_track(dict(
        id=sc_id or i, kind="track", title=f"Track {i}", permalink_url=f"https://soundcloud.com/a/t{i}",
        user={"username": f"Artist{i}", "permalink_url": "https://soundcloud.com/a"},
        created_at=NOW, playback_count=1000, likes_count=50, reposts_count=5, comment_count=1,
        downloadable=False, tag_list="", description=""))


# ---------------- Speicherung ----------------
@pytest.fixture
def db(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as d:
        yield d


def test_feedback_roundtrip_and_last_choice_wins(db):
    assert db.get_sc_feedback(123) is None
    db.set_sc_feedback(123, "like", url="https://soundcloud.com/a/t1")
    assert db.get_sc_feedback(123) == "like"
    db.set_sc_feedback(123, "later")
    assert db.get_sc_feedback(123) == "later"
    row = db.db.execute("SELECT url FROM sc_feedback WHERE sc_id = 123").fetchone()
    assert row["url"] == "https://soundcloud.com/a/t1"   # url bleibt, wenn später keine kommt


def test_feedback_rejects_unknown_values(db):
    with pytest.raises(ValueError):
        db.set_sc_feedback(1, "love")


def test_feedback_table_comes_from_versioned_migration(db):
    versions = [r[0] for r in db.db.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert versions[:2] == [1, 2]


# ---------------- Callback-Format ----------------
def test_callback_data_roundtrip_and_limit():
    data = out.feedback_callback_data("dislike", 12345678901234567890)
    assert data == "fb:dislike:12345678901234567890"
    assert len(data.encode()) <= 64
    assert out.parse_feedback_callback(data) == ("dislike", 12345678901234567890)


@pytest.mark.parametrize("bad", ["", "fb:like", "fb:love:1", "xx:like:1", "fb:like:abc", "fb:like:-1", None])
def test_parse_rejects_garbage(bad):
    assert out.parse_feedback_callback(bad) is None


# ---------------- Digest mit Nummern und Tastatur ----------------
def test_plain_digest_is_unchanged():
    tracks = [mk(i) for i in range(1, 4)]
    assert out.build_digest(tracks, None, header="T") == [
        msg.text for msg in out.build_digest_messages(tracks, None, header="T", numbered=False)]
    assert "1." not in out.build_digest(tracks, None, header="T")[0]


def test_numbered_digest_has_one_keyboard_row_per_track():
    tracks = [mk(i, sc_id=1000 + i) for i in range(1, 4)]
    (msg,) = out.build_digest_messages(tracks, None, header="T", numbered=True)
    assert msg.items == [(1, 1001), (2, 1002), (3, 1003)]
    assert re.search(r"<b>1\.</b> .*Track 1", msg.text) and re.search(r"<b>3\.</b> .*Track 3", msg.text)
    kb = out.feedback_keyboard(msg.items)
    assert kb["inline_keyboard"][1] == [
        {"text": "2 👍", "callback_data": "fb:like:1002"},
        {"text": "2 👎", "callback_data": "fb:dislike:1002"},
        {"text": "2 ⏳", "callback_data": "fb:later:1002"},
    ]
    assert out.feedback_keyboard([]) is None


def test_numbering_continues_across_messages_and_max_10_per_message():
    tracks = [mk(i) for i in range(1, 26)]
    msgs = out.build_digest_messages(tracks, None, header="T", numbered=True)
    assert all(len(msg.items) <= 10 for msg in msgs)
    numbers = [n for msg in msgs for n, _ in msg.items]
    assert numbers == list(range(1, 26))
    assert all(len(msg.text) <= 4096 for msg in msgs)


def test_only_shown_tracks_get_buttons():
    tracks = [mk(i) for i in range(1, 6)]
    msgs = out.build_digest_messages(tracks, 3, header="T", numbered=True)
    assert [sc for msg in msgs for _, sc in msg.items] == [1, 2, 3]
    assert "2 weitere" in msgs[-1].text


# ---------------- Versand ----------------
def _cfg(tmp_path, buttons: bool) -> Config:
    c = Config.load(ROOT / "config.yaml")
    c.raw["telegram"] = {**c["telegram"], "feedback_buttons": buttons}
    return c


def test_send_digest_attaches_keyboard(monkeypatch):
    calls = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(out, "telegram_call", lambda token, method, **kw: calls.append((method, kw["json"])) or {"ok": True})
    msgs = out.build_digest_messages([mk(1), mk(2)], None, header="T", numbered=True)
    out.send_digest(Config.load(ROOT / "config.yaml"), msgs, buttons=True)
    method, payload = calls[0]
    assert method == "sendMessage" and payload["chat_id"] == "42" and payload["parse_mode"] == "HTML"
    assert payload["reply_markup"] == out.feedback_keyboard(msgs[0].items)


def test_deliver_sends_buttons_only_when_enabled(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(m, "send_digest", lambda cfg, msgs, chat_id=None, *, buttons: sent.append((buttons, msgs)))
    monkeypatch.setattr(m, "send_telegram_document", lambda *a, **k: None)
    for flag in (True, False):
        m.deliver("H", [mk(1), mk(2)], [mk(3)], _cfg(tmp_path, flag), dry_run=False, no_telegram=False)
    (on, msgs_on), (off, msgs_off) = sent
    assert on is True and msgs_on[0].items == [(1, 1), (2, 2)]
    assert off is False
    assert "1 bereits in deiner Sammlung" in msgs_on[-1].text


def test_config_enables_buttons_by_default():
    assert Config.load(ROOT / "config.yaml")["telegram"]["feedback_buttons"] is True
