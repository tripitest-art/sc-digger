"""Telegram-Bot-Listener für On-Demand-Checks: Playlist oder Track-Station (Algorithmus).

Läuft dauerhaft neben dem täglichen Cron-Job. Reagiert nur auf Nachrichten aus dem
konfigurierten TELEGRAM_CHAT_ID (kein offener Bot). Schickt man einen SoundCloud-Link,
bekommt man den kompletten Playlist-Inhalt bzw. die "Station" (das, was SoundCloud
per Algorithmus zu einem Track als Radio vorschlägt) zurück – gleiches Format wie
der tägliche Digest (Chat-Nachricht + Export-Datei), aber ohne Perzentil-Filter.

Wichtig für den Betrieb: Der Bot ist Hauptprozess des Containers. Beendet er sich,
stirbt auch der Cron für den täglichen Lauf. Deshalb beendet er sich nie von selbst,
auch nicht bei fehlenden Zugangsdaten oder Telegram-Ausfällen.
"""
from __future__ import annotations

import logging
import re
import time

from .db import TrackDB
from .main import run_link
from .models import Config
from .output import TelegramError, parse_feedback_callback, telegram_call
from .redact import install_redacting_logging
from .soundcloud import SoundCloudClient, SoundCloudError

log = logging.getLogger("sc_digger.bot")

URL_RE = re.compile(r"https?://(?:on\.)?(?:www\.|m\.)?soundcloud\.com/\S+", re.I)

HELP_TEXT = (
    "sc-digger Bot\n\n"
    "Schick mir einen SoundCloud-Link:\n"
    "• Playlist/Set-Link -> ich zeige dir alle Tracks der Playlist\n"
    "• einzelner Track-Link -> ich zeige dir die 'Station' dazu "
    "(das, was der SoundCloud-Algorithmus als Radio vorschlägt)\n\n"
    "Kein täglicher Filter, du bekommst die volle Liste mit Stats und Download-Einordnung."
)


def _send_text(cfg: Config, chat_id: str, text: str) -> None:
    telegram_call(cfg.telegram_token, "sendMessage",
                  json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                        "disable_web_page_preview": True})


def handle_message(cfg: Config, sc: SoundCloudClient, chat_id: str, text: str) -> None:
    text = text.strip()
    if text in ("/start", "/help"):
        _send_text(cfg, chat_id, HELP_TEXT)
        return

    m = URL_RE.search(text)
    if not m:
        _send_text(cfg, chat_id, "Kein SoundCloud-Link erkannt.\n\n" + HELP_TEXT)
        return

    url = m.group(0)
    _send_text(cfg, chat_id, f"Prüfe {url} ...")
    try:
        run_link(cfg, url, chat_id=chat_id, sc=sc)  # schickt Digest + Export selbst
    except SoundCloudError as e:
        log.warning("SoundCloudError bei On-Demand-Check für %s: %s", url, e)
        _send_text(cfg, chat_id, f"Fehler: {e}")
        return
    except Exception:
        log.exception("Fehler bei On-Demand-Check für %s", url)
        _send_text(cfg, chat_id, "Da ist beim Verarbeiten etwas schiefgelaufen, siehe Container-Log.")


FEEDBACK_EMOJI: dict[str, str] = {
    "like": "👍",
    "dislike": "👎",
    "later": "⏳",
}


def mark_choice(keyboard: dict, sc_id: int, value: str) -> dict:
    """Gibt eine KOPIE zurück (Eingabe unverändert). In der Reihe, deren Buttons zu sc_id gehören:
    alle Texte ohne führendes "✅ ", der Button mit (value, sc_id) bekommt "✅ " vorangestellt.
    callback_data und alle anderen Reihen bleiben gleich."""
    if not isinstance(keyboard, dict):
        return {}
    res = {k: v for k, v in keyboard.items() if k != "inline_keyboard"}
    new_rows = []
    for row in keyboard.get("inline_keyboard", []):
        is_target_row = any(
            (p := parse_feedback_callback(b.get("callback_data"))) is not None and p[1] == sc_id
            for b in row
        )
        if not is_target_row:
            new_rows.append([dict(b) for b in row])
            continue
        new_row = []
        for b in row:
            btn = dict(b)
            txt = btn.get("text", "").removeprefix("✅ ")
            p = parse_feedback_callback(btn.get("callback_data"))
            if p == (value, sc_id):
                txt = f"✅ {txt}"
            btn["text"] = txt
            new_row.append(btn)
        new_rows.append(new_row)
    res["inline_keyboard"] = new_rows
    return res


def handle_callback(cfg: Config, cq: dict) -> None:
    """Ein callback_query-Objekt von Telegram. Wirft nie.
    1. cq["message"]["chat"]["id"] != TELEGRAM_CHAT_ID -> log.warning, KEIN Telegram-Aufruf, nichts speichern.
    2. parse_feedback_callback(cq["data"]) ist None -> answerCallbackQuery(callback_query_id, text="Unbekannte Aktion"), nichts speichern.
    3. with TrackDB(cfg["state"]["track_db_path"]) as db: db.set_sc_feedback(sc_id, value)
       Fehler -> log.warning, answerCallbackQuery mit Text, der "nicht gespeichert" enthält; Ende.
    4. answerCallbackQuery(callback_query_id, text="<Emoji> gespeichert")   (👍/👎/⏳)
    5. editMessageReplyMarkup(chat_id=<chat id wie im cq>, message_id=cq["message"]["message_id"],
       reply_markup=mark_choice(cq["message"]["reply_markup"], sc_id, value))
    Alle Aufrufe über telegram_call(cfg.telegram_token, "<Methode>", json={...})."""
    try:
        msg = cq.get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        if not cfg.telegram_chat_id or str(chat_id) != str(cfg.telegram_chat_id):
            log.warning("Callback aus fremdem Chat ignoriert: id=%s", chat_id)
            return

        cq_id = cq.get("id")
        parsed = parse_feedback_callback(cq.get("data"))
        if parsed is None:
            if cq_id:
                try:
                    telegram_call(
                        cfg.telegram_token,
                        "answerCallbackQuery",
                        json={"callback_query_id": cq_id, "text": "Unbekannte Aktion"},
                    )
                except Exception as e:
                    log.warning("Telegram-Fehler bei answerCallbackQuery: %s", e)
            return

        value, sc_id = parsed
        try:
            with TrackDB(cfg["state"]["track_db_path"]) as db:
                db.set_sc_feedback(sc_id, value)
        except Exception as e:
            log.warning("Fehler beim Speichern des Feedbacks fuer Track %s: %s", sc_id, e)
            if cq_id:
                try:
                    telegram_call(
                        cfg.telegram_token,
                        "answerCallbackQuery",
                        json={"callback_query_id": cq_id, "text": "Feedback nicht gespeichert"},
                    )
                except Exception as te:
                    log.warning("Telegram-Fehler bei answerCallbackQuery: %s", te)
            return

        emoji = FEEDBACK_EMOJI.get(value, "")
        if cq_id:
            try:
                telegram_call(
                    cfg.telegram_token,
                    "answerCallbackQuery",
                    json={"callback_query_id": cq_id, "text": f"{emoji} gespeichert".strip()},
                )
            except Exception as e:
                log.warning("Telegram-Fehler bei answerCallbackQuery: %s", e)

        reply_markup = msg.get("reply_markup")
        if reply_markup and "message_id" in msg:
            try:
                new_kb = mark_choice(reply_markup, sc_id, value)
                telegram_call(
                    cfg.telegram_token,
                    "editMessageReplyMarkup",
                    json={
                        "chat_id": chat_id,
                        "message_id": msg["message_id"],
                        "reply_markup": new_kb,
                    },
                )
            except Exception as e:
                log.warning("Telegram-Fehler bei editMessageReplyMarkup: %s", e)
    except Exception:
        log.exception("Unerwarteter Fehler bei Callback-Verarbeitung")


def _idle_forever(reason: str) -> None:
    """Hält den Container am Leben, damit der tägliche Cron-Lauf weiterläuft."""
    log.warning("%s – Bot pausiert, der tägliche Lauf läuft trotzdem.", reason)
    while True:
        time.sleep(3600)


def listen(cfg: Config) -> None:
    token, chat_id = cfg.telegram_token, cfg.telegram_chat_id
    if not token or not chat_id:
        _idle_forever("TELEGRAM_BOT_TOKEN/CHAT_ID fehlen")

    if not str(chat_id).lstrip("-").isdigit():
        log.warning("TELEGRAM_CHAT_ID %r ist keine Zahl (Bot-Name statt Chat-ID?). Schreib dem Bot "
                    "eine Nachricht, die richtige ID erscheint dann hier im Log.", chat_id)

    sc = SoundCloudClient()

    # Beim Start: Backlog verwerfen, nicht beim Hochfahren alte Nachrichten abarbeiten.
    # Bei Netzproblemen wiederholen statt abzustürzen.
    while True:
        try:
            results = telegram_call(token, "getUpdates", http="get", params={"timeout": 0})["result"]
            offset = (results[-1]["update_id"] + 1) if results else 0
            break
        except TelegramError as e:
            if "Telegram 401" in str(e):
                _idle_forever("Telegram lehnt den Bot-Token ab (401)")
            log.warning("Telegram nicht erreichbar (%s), neuer Versuch in 30 s", e)
            time.sleep(30)

    log.info("Bot-Listener gestartet, warte auf Nachrichten (Chat %s)...", chat_id)
    while True:
        try:
            updates = telegram_call(token, "getUpdates", http="get", timeout=40,
                                    params={"offset": offset, "timeout": 30})["result"]
            for upd in updates:
                offset = upd["update_id"] + 1
                if upd.get("callback_query"):
                    handle_callback(cfg, upd["callback_query"])
                    continue
                msg = upd.get("message") or {}
                chat = msg.get("chat", {})
                if str(chat.get("id", "")) != str(chat_id):
                    # Nur ID/Typ/Vorname loggen, keinen Nachrichtentext: hilft beim Einrichten
                    log.warning("Nachricht aus fremdem Chat ignoriert: id=%s typ=%s name=%s",
                                chat.get("id"), chat.get("type"),
                                chat.get("first_name") or chat.get("title"))
                    continue
                if msg.get("text"):
                    handle_message(cfg, sc, chat_id, msg["text"])
        except TelegramError as e:
            log.warning("Telegram-Polling-Fehler (%s), versuche erneut", e)
            time.sleep(5)
        except Exception:
            log.exception("Unerwarteter Fehler im Bot-Loop")
            time.sleep(5)


def cli() -> None:
    install_redacting_logging(logging.INFO)
    listen(Config.load("config.yaml"))


if __name__ == "__main__":
    cli()
