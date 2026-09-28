"""Akzeptanztests: Der Bot verarbeitet 👍/👎/⏳-Klicks aus dem Digest."""
from pathlib import Path

import pytest

from sc_digger import bot
from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.output import feedback_keyboard

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    c = Config.load(ROOT / "config.yaml")
    c.raw["state"] = {**c["state"], "track_db_path": str(tmp_path / "tracks.sqlite")}
    return c


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake(token, method, **kw):
        seen.append((method, kw.get("json") or kw.get("params")))
        return {"ok": True, "result": True}

    monkeypatch.setattr(bot, "telegram_call", fake)
    return seen


def cq(data: str, chat_id: int = 42, cq_id: str = "cb1"):
    return {"id": cq_id, "data": data,
            "message": {"message_id": 7, "chat": {"id": chat_id},
                        "reply_markup": feedback_keyboard([(1, 111), (2, 222)])}}


def stored(cfg, sc_id):
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        return db.get_sc_feedback(sc_id)


# ---------------- Markierung der Wahl ----------------
def test_mark_choice_marks_only_the_chosen_button_of_that_track():
    kb = feedback_keyboard([(1, 111), (2, 222)])
    marked = bot.mark_choice(kb, 222, "dislike")
    assert [b["text"] for b in marked["inline_keyboard"][1]] == ["2 👍", "✅ 2 👎", "2 ⏳"]
    assert marked["inline_keyboard"][0] == kb["inline_keyboard"][0]
    again = bot.mark_choice(marked, 222, "like")
    assert [b["text"] for b in again["inline_keyboard"][1]] == ["✅ 2 👍", "2 👎", "2 ⏳"]
    assert [b["callback_data"] for b in again["inline_keyboard"][1]] == [
        b["callback_data"] for b in kb["inline_keyboard"][1]]
    assert kb["inline_keyboard"][1][1]["text"] == "2 👎"   # Eingabe nicht verändert


# ---------------- Klick verarbeiten ----------------
def test_click_is_stored_answered_and_marked(cfg, calls):
    bot.handle_callback(cfg, cq("fb:like:222"))
    assert stored(cfg, 222) == "like"
    methods = [c[0] for c in calls]
    assert methods == ["answerCallbackQuery", "editMessageReplyMarkup"]
    answer, edit = calls[0][1], calls[1][1]
    assert answer["callback_query_id"] == "cb1" and "👍" in answer["text"]
    assert edit["chat_id"] == 42 and edit["message_id"] == 7
    assert edit["reply_markup"]["inline_keyboard"][1][0]["text"] == "✅ 2 👍"


def test_click_from_foreign_chat_is_ignored(cfg, calls):
    bot.handle_callback(cfg, cq("fb:like:222", chat_id=666))
    assert stored(cfg, 222) is None
    assert calls == []


def test_garbage_callback_is_answered_but_not_stored(cfg, calls):
    bot.handle_callback(cfg, cq("fb:love:222"))
    assert stored(cfg, 222) is None
    assert [c[0] for c in calls] == ["answerCallbackQuery"]


def test_storage_failure_is_reported_not_raised(cfg, calls, monkeypatch):
    def boom(*a, **k):
        raise OSError("Platte voll")
    monkeypatch.setattr(TrackDB, "set_sc_feedback", boom)
    bot.handle_callback(cfg, cq("fb:later:222"))
    assert [c[0] for c in calls] == ["answerCallbackQuery"]
    assert "nicht gespeichert" in calls[0][1]["text"]


# ---------------- Einbindung in den Polling-Loop ----------------
def test_listen_dispatches_callbacks_without_foreign_chat_warning(cfg, monkeypatch, caplog):
    handled = []
    updates = [[], [{"update_id": 5, "callback_query": cq("fb:like:111")}]]

    def fake_call(token, method, **kw):
        if method == "getUpdates":
            if not updates:
                raise KeyboardInterrupt
            return {"ok": True, "result": updates.pop(0)}
        return {"ok": True, "result": True}

    monkeypatch.setattr(bot, "telegram_call", fake_call)
    monkeypatch.setattr(bot, "SoundCloudClient", lambda: None)
    monkeypatch.setattr(bot, "handle_callback", lambda c, q: handled.append(q["data"]))
    with pytest.raises(KeyboardInterrupt):
        bot.listen(cfg)
    assert handled == ["fb:like:111"]
    assert "fremdem Chat" not in caplog.text
