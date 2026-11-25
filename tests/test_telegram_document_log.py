"""Tests für die Erfolgszeile beim Export-Anhang an Telegram."""
import logging

from sc_digger import output as out
from sc_digger.models import Config

ERFOLG = "Export-Anhang gesendet"


def _antwort(message_id=4711):
    return {"ok": True, "result": {"message_id": message_id}}


def _erfolgszeilen(caplog):
    return [r for r in caplog.records
            if r.name == "sc_digger.output" and r.levelno == logging.INFO
            and r.getMessage().startswith(ERFOLG)]


def test_bytes_zaehlen_umlaute_korrekt(monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(out, "telegram_call", lambda *a, **k: _antwort())
    with caplog.at_level(logging.INFO, logger="sc_digger.output"):
        out.send_telegram_document(Config({}), "d.txt", "ä\n", chat_id="123")
    assert "Export-Anhang gesendet: d.txt (3 Bytes, message_id=4711)" in caplog.text


def test_genau_eine_zeile_pro_aufruf(monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(out, "telegram_call", lambda *a, **k: _antwort())
    with caplog.at_level(logging.INFO, logger="sc_digger.output"):
        out.send_telegram_document(Config({}), "d.txt", "x", chat_id="123")
    assert len(_erfolgszeilen(caplog)) == 1


def test_chat_id_aus_parameter_schlaegt_umgebung(monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "aus-umgebung")
    gesehen = {}

    def merken(token, method, **kwargs):
        gesehen.update(kwargs["data"])
        return _antwort()

    monkeypatch.setattr(out, "telegram_call", merken)
    with caplog.at_level(logging.INFO, logger="sc_digger.output"):
        out.send_telegram_document(Config({}), "d.txt", "x", chat_id="aus-parameter")
    assert gesehen["chat_id"] == "aus-parameter"
