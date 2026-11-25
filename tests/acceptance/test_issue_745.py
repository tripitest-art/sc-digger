"""Akzeptanztests: Erfolg des Export-Anhangs wird als INFO-Zeile geloggt."""
import logging

import pytest

from sc_digger import output as out
from sc_digger.models import Config

ERFOLG = "Export-Anhang gesendet"


def _antwort(message_id=4711):
    return {"ok": True, "result": {"message_id": message_id}}


def _mit_token(monkeypatch, antwort=None):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(
        out, "telegram_call",
        lambda *a, **k: _antwort() if antwort is None else antwort,
    )
    return Config({})


def test_erfolg_loggt_dateiname_bytes_und_message_id(monkeypatch, caplog):
    cfg = _mit_token(monkeypatch)
    with caplog.at_level(logging.INFO, logger="sc_digger.output"):
        out.send_telegram_document(cfg, "sc-digger-tag-2026-11-26_0830.txt", "abc\n", chat_id="123")
    assert f"{ERFOLG}: sc-digger-tag-2026-11-26_0830.txt (4 Bytes, message_id=4711)" in caplog.text


def test_fehlende_message_id_wird_als_none_geloggt(monkeypatch, caplog):
    cfg = _mit_token(monkeypatch, antwort={"ok": True, "result": {}})
    with caplog.at_level(logging.INFO, logger="sc_digger.output"):
        out.send_telegram_document(cfg, "d.txt", "x", chat_id="123")
    assert "message_id=None" in caplog.text


def test_ohne_token_keine_erfolgszeile(monkeypatch, caplog):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    gerufen = []
    monkeypatch.setattr(out, "telegram_call", lambda *a, **k: gerufen.append(1) or _antwort())
    with caplog.at_level(logging.INFO, logger="sc_digger.output"):
        out.send_telegram_document(Config({}), "d.txt", "x", chat_id="123")
    assert ERFOLG not in caplog.text
    assert gerufen == []


def test_telegram_fehler_loggt_keine_erfolgszeile(monkeypatch, caplog):
    def kaputt(*a, **k):
        raise out.TelegramError("sendDocument: Telegram 400: Bad Request")

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(out, "telegram_call", kaputt)
    with caplog.at_level(logging.INFO, logger="sc_digger.output"):
        with pytest.raises(out.TelegramError):
            out.send_telegram_document(Config({}), "d.txt", "x", chat_id="123")
    assert ERFOLG not in caplog.text
