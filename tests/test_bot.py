"""Zusätzliche Tests für Bot-Funktionen (Feedback-Callback-Handling und mark_choice)."""
from pathlib import Path

import pytest

from sc_digger import bot
from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.output import feedback_keyboard

ROOT = Path(__file__).resolve().parents[1]


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


def test_click_without_reply_markup_stores_and_answers_without_edit(cfg, calls):
    """Klick auf einen Button ohne reply_markup speichert und antwortet, ruft kein editMessageReplyMarkup auf."""
    cq = {
        "id": "cb_no_markup",
        "data": "fb:like:333",
        "message": {
            "message_id": 12,
            "chat": {"id": 42},
        },
    }
    bot.handle_callback(cfg, cq)
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        assert db.get_sc_feedback(333) == "like"
    assert [c[0] for c in calls] == ["answerCallbackQuery"]
    assert calls[0][1]["callback_query_id"] == "cb_no_markup"
    assert "👍" in calls[0][1]["text"]


def test_click_with_none_reply_markup_stores_and_answers_without_edit(cfg, calls):
    """Klick mit explizitem reply_markup=None speichert und antwortet, ruft kein editMessageReplyMarkup auf."""
    cq = {
        "id": "cb_none_markup",
        "data": "fb:dislike:444",
        "message": {
            "message_id": 13,
            "chat": {"id": 42},
            "reply_markup": None,
        },
    }
    bot.handle_callback(cfg, cq)
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        assert db.get_sc_feedback(444) == "dislike"
    assert [c[0] for c in calls] == ["answerCallbackQuery"]
    assert calls[0][1]["callback_query_id"] == "cb_none_markup"
    assert "👎" in calls[0][1]["text"]


def test_mark_choice_with_unknown_track_returns_unmodified_copy():
    kb = feedback_keyboard([(1, 111), (2, 222)])
    marked = bot.mark_choice(kb, 999, "like")
    assert marked == kb
    assert marked is not kb


def test_mark_choice_invalid_input():
    assert bot.mark_choice(None, 111, "like") == {}
    assert bot.mark_choice({}, 111, "like") == {"inline_keyboard": []}


def test_handle_callback_handles_telegram_error_gracefully(cfg, monkeypatch):
    def boom(*a, **k):
        raise bot.TelegramError("Netzwerkfehler")

    monkeypatch.setattr(bot, "telegram_call", boom)
    cq = {
        "id": "cb_err",
        "data": "fb:like:555",
        "message": {
            "message_id": 14,
            "chat": {"id": 42},
            "reply_markup": feedback_keyboard([(1, 555)]),
        },
    }
    # Wirft nie, selbst bei Fehlern in telegram_call
    bot.handle_callback(cfg, cq)
