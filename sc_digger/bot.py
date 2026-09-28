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

import requests

from .main import run_link
from .models import Config
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
    requests.post(
        f"https://api.telegram.org/bot{cfg.telegram_token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=20,
    ).raise_for_status()


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


def _idle_forever(reason: str) -> None:
    """Hält den Container am Leben, damit der tägliche Cron-Lauf weiterläuft."""
    log.warning("%s – Bot pausiert, der tägliche Lauf läuft trotzdem.", reason)
    while True:
        time.sleep(3600)


def listen(cfg: Config) -> None:
    token, chat_id = cfg.telegram_token, cfg.telegram_chat_id
    if not token or not chat_id:
        _idle_forever("TELEGRAM_BOT_TOKEN/CHAT_ID fehlen")

    sc = SoundCloudClient()

    # Beim Start: Backlog verwerfen, nicht beim Hochfahren alte Nachrichten abarbeiten.
    # Bei Netzproblemen wiederholen statt abzustürzen.
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                             params={"timeout": 0}, timeout=20)
            if r.status_code == 401:
                _idle_forever("Telegram lehnt den Bot-Token ab (401)")
            r.raise_for_status()
            results = r.json().get("result", [])
            offset = (results[-1]["update_id"] + 1) if results else 0
            break
        except requests.RequestException:
            log.warning("Telegram nicht erreichbar, neuer Versuch in 30 s", exc_info=True)
            time.sleep(30)

    log.info("Bot-Listener gestartet, warte auf Nachrichten (Chat %s)...", chat_id)
    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": offset, "timeout": 30}, timeout=40,
            )
            r.raise_for_status()
            for upd in r.json().get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                if str(msg.get("chat", {}).get("id", "")) != str(chat_id):
                    continue
                if msg.get("text"):
                    handle_message(cfg, sc, chat_id, msg["text"])
        except requests.RequestException:
            log.warning("Telegram-Polling-Fehler, versuche erneut", exc_info=True)
            time.sleep(5)
        except Exception:
            log.exception("Unerwarteter Fehler im Bot-Loop")
            time.sleep(5)


def cli() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    listen(Config.load("config.yaml"))


if __name__ == "__main__":
    cli()
