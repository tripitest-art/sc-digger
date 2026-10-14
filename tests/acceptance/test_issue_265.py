from sc_digger import bot
from sc_digger.models import Config
from sc_digger.output import TelegramError

def test_help_text_has_no_raw_angle_bracket_tags():
    # Keine unmaskierten spitzen Klammern, die Telegram als HTML-Tags missversteht
    assert "<Suchtext" not in bot.HELP_TEXT
    assert "<SoundCloud-ID>" not in bot.HELP_TEXT
    assert "&lt;Suchtext" in bot.HELP_TEXT or "<code>" in bot.HELP_TEXT

def test_handle_message_routes_help_with_botname(monkeypatch):
    cfg = Config({"telegram": {"bot_token": "tok", "chat_id": "42"}})
    sent = []
    def fake_send(c, chat_id, msg):
        sent.append((chat_id, msg))
    monkeypatch.setattr(bot, "_send_text", fake_send)
    
    # Normaler /help Befehl
    bot.handle_message(cfg, None, "42", "/help")
    assert len(sent) == 1
    assert sent[0] == ("42", bot.HELP_TEXT)

    # /help mit Bot-Namen (z. B. in Gruppen oder bei Autovervollständigung)
    sent.clear()
    bot.handle_message(cfg, None, "42", "/help@sc_digger_bot")
    assert len(sent) == 1
    assert sent[0] == ("42", bot.HELP_TEXT)

    # /start mit Bot-Namen
    sent.clear()
    bot.handle_message(cfg, None, "42", "/start@sc_digger_bot")
    assert len(sent) == 1
    assert sent[0] == ("42", bot.HELP_TEXT)

def test_send_text_falls_back_to_plain_text_on_html_error(monkeypatch):
    cfg = Config({"telegram": {"bot_token": "tok", "chat_id": "42"}})
    calls = []

    def fake_telegram_call(token, method, **kw):
        calls.append((method, kw.get("json", {})))
        payload = kw.get("json", {})
        if payload.get("parse_mode") == "HTML":
            raise TelegramError("sendMessage: Telegram 400: Bad Request: can't parse entities: Unsupported start tag")
        return {"ok": True}

    monkeypatch.setattr(bot, "telegram_call", fake_telegram_call)
    bot._send_text(cfg, "42", "Ungültiger <Text>")

    assert len(calls) == 2
    assert calls[0][1]["parse_mode"] == "HTML"
    assert "parse_mode" not in calls[1][1] or calls[1][1]["parse_mode"] is None
    assert calls[1][1]["text"] == "Ungültiger <Text>"
