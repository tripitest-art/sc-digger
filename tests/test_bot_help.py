"""Zusätzliche Tests für /help und /start im Telegram-Bot (Issue #265).

Deckt die HTML-Maskierung des Hilfetexts, das Erkennen von Befehlen mit angehängtem
Bot-Namen und den Klartext-Fallback in `_send_text` ab.
"""
import pytest

from sc_digger import bot
from sc_digger.models import Config
from sc_digger.output import TelegramError


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    return Config({"telegram": {"bot_token": "123:abc", "chat_id": "42"}})


def test_help_text_keeps_raw_angle_brackets_out_of_html(cfg):
    """Der Hilfetext darf keine spitzen Klammern enthalten, die Telegram als Tag liest."""
    assert "<Suchtext" not in bot.HELP_TEXT
    assert "<SoundCloud-ID>" not in bot.HELP_TEXT
    assert "&lt;Suchtext oder id&gt;" in bot.HELP_TEXT
    assert "&lt;SoundCloud-ID&gt;" in bot.HELP_TEXT


@pytest.mark.parametrize(
    "text",
    ["/help", "/start", "/help@sc_digger_bot", "/start@sc_digger_bot",
     "/help @sc_digger_bot", "/start @sc_digger_bot", "  /help  ", "/help@Bot   "],
)
def test_handle_message_sends_help_for_all_command_variants(cfg, monkeypatch, text):
    """Alle Schreibweisen (auch mit Bot-Namen und Leerzeichen) liefern den Hilfetext."""
    sent = []
    monkeypatch.setattr(bot, "_send_text", lambda c, chat_id, msg: sent.append((chat_id, msg)))

    bot.handle_message(cfg, None, "42", text)

    assert sent == [("42", bot.HELP_TEXT)]


def test_send_text_falls_back_on_400(monkeypatch):
    """Bei Telegram 400 wird die Nachricht ohne parse_mode erneut gesendet."""
    calls = []

    def fake_telegram_call(token, method, **kw):
        payload = kw.get("json", {})
        calls.append(payload)
        if payload.get("parse_mode") == "HTML":
            raise TelegramError("sendMessage: Telegram 400: Bad Request: can't parse entities")
        return {"ok": True}

    monkeypatch.setattr(bot, "telegram_call", fake_telegram_call)
    bot._send_text(Config({"telegram": {}}), "42", "Ungültiger <Text>")

    assert len(calls) == 2
    assert calls[0]["parse_mode"] == "HTML"
    assert calls[1].get("parse_mode") is None
    assert calls[1]["text"] == "Ungültiger <Text>"


def test_send_text_falls_back_on_entity_error_without_400(monkeypatch):
    """Auch ein Entity-Fehler ohne explizite 400 läuft in den Klartext-Fallback."""
    calls = []

    def fake_telegram_call(token, method, **kw):
        payload = kw.get("json", {})
        calls.append(payload)
        if payload.get("parse_mode") == "HTML":
            raise TelegramError("sendMessage: can't parse entities: Unsupported start tag")
        return {"ok": True}

    monkeypatch.setattr(bot, "telegram_call", fake_telegram_call)
    bot._send_text(Config({"telegram": {}}), "42", "a < b")

    assert len(calls) == 2
    assert calls[1].get("parse_mode") is None
    assert calls[1]["text"] == "a < b"


def test_send_text_reraises_non_html_errors(monkeypatch):
    """Andere Telegram-Fehler werden nicht verschluckt, es gibt keinen zweiten Versuch."""
    calls = []

    def fake_telegram_call(token, method, **kw):
        calls.append(kw.get("json", {}))
        raise TelegramError("sendMessage: Telegram 401: Unauthorized")

    monkeypatch.setattr(bot, "telegram_call", fake_telegram_call)

    with pytest.raises(TelegramError):
        bot._send_text(Config({"telegram": {}}), "42", "text")

    assert len(calls) == 1
