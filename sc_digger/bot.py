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

import html
import logging
import re
import time
from collections import Counter

from .db import TrackDB
from .harmonic import compatible_keys, find_mix_candidates, format_mix_list
from .main import run_link
from .models import Config
from .output import TelegramError, parse_feedback_callback, telegram_call
from . import output
from .redact import install_redacting_logging
from .soundcloud import SoundCloudClient, SoundCloudError

log = logging.getLogger("sc_digger.bot")

URL_RE = re.compile(r"https?://(?:on\.)?(?:www\.|m\.)?soundcloud\.com/\S+", re.I)

MIX_USAGE = "Aufruf: /mix <Camelot-Key> <BPM> [Toleranz], z. B. /mix 5A 155 oder /mix 8B 160 2"

HELP_TEXT = (
    "sc-digger Bot\n\n"
    "Schick mir einen SoundCloud-Link:\n"
    "• Playlist/Set-Link -> ich zeige dir alle Tracks der Playlist\n"
    "• einzelner Track-Link -> ich zeige dir die 'Station' dazu "
    "(das, was der SoundCloud-Algorithmus als Radio vorschlägt)\n\n"
    "Befehle:\n"
    "• /kaufliste -> zeigt die aktuelle Kaufliste offener Store-Tracks\n"
    "• /curator_mining -> Profile aus 👍-Tracks vorschlagen\n"
    "• /mix 5A 155 -> harmonisch passende Tracks aus der Sammlung (±3 BPM)\n\n"
    "Kein täglicher Filter, du bekommst die volle Liste mit Stats und Download-Einordnung."
)


def _send_text(cfg: Config, chat_id: str, text: str) -> None:
    telegram_call(cfg.telegram_token, "sendMessage",
                  json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                        "disable_web_page_preview": True})


def handle_kaufliste_command(cfg: Config, chat_id: str | None = None) -> None:
    """Bearbeitet den /kaufliste-Befehl und sendet die aktuelle Kaufliste."""
    target_chat = chat_id or cfg.telegram_chat_id or ""
    try:
        if chat_id is not None:
            output.send_kaufliste(cfg, chat_id=chat_id)
        else:
            output.send_kaufliste(cfg)
    except Exception:
        log.exception("Fehler beim Senden der Kaufliste")
        if target_chat:
            _send_text(cfg, target_chat, "Kaufliste fehlgeschlagen, siehe Container-Log.")


def run_curator_mining(sc, cfg: Config) -> str:
    """Aggregiert Liker/Reposter über alle 👍-Tracks, filtert bekannte Accounts,
    gibt formatierten Text zurück. Kein Telegram-Aufruf (macht der Bot-Handler)."""
    track_db_path = cfg.raw.get("state", {}).get("track_db_path")
    if not track_db_path:
        return "Keine neuen Curator-Vorschläge."

    with TrackDB(track_db_path) as db:
        sc_ids = db.get_liked_sc_ids()

    if not sc_ids:
        return "Keine neuen Curator-Vorschläge."

    cm_cfg = cfg.raw.get("curator_mining") or {}
    min_appearances = int(cm_cfg.get("min_appearances", 2))
    max_likers = int(cm_cfg.get("max_likers_per_track", 50))

    search_cfg = cfg.raw.get("search") or {}
    ref_accounts = search_cfg.get("reference_accounts") or []
    followed_users = search_cfg.get("followed_users") or []
    known = set()
    for item in ref_accounts + followed_users:
        if not item:
            continue
        slug = str(item).strip().rstrip("/").split("/")[-1].lower()
        if slug:
            known.add(slug)

    counter: Counter[str] = Counter()
    usernames: dict[str, str] = {}
    original_permalinks: dict[str, str] = {}

    for sc_id in sc_ids:
        likers = sc.get_likers(sc_id, max_results=max_likers) or []
        reposters = sc.get_reposters(sc_id, max_results=max_likers) or []
        for u in likers + reposters:
            permalink = u.get("permalink")
            if not permalink:
                continue
            p_clean = str(permalink).strip().rstrip("/").split("/")[-1]
            p_lower = p_clean.lower()
            if p_lower in known:
                continue
            counter[p_lower] += 1
            if p_lower not in original_permalinks:
                original_permalinks[p_lower] = p_clean
            if p_lower not in usernames:
                usernames[p_lower] = u.get("username") or p_clean

    candidates = [
        (p_lower, count)
        for p_lower, count in counter.most_common()
        if count >= min_appearances
    ]

    if not candidates:
        return "Keine neuen Curator-Vorschläge."

    max_display = 30
    lines = ["🔍 Curator-Vorschläge", ""]
    for p_lower, count in candidates[:max_display]:
        permalink = original_permalinks[p_lower]
        username = html.escape(usernames.get(p_lower, permalink))
        lines.append(f"• {username} (soundcloud.com/{permalink}) – {count}× gesehen")

    if len(candidates) > max_display:
        lines.append(f"… und {len(candidates) - max_display} weitere")

    return "\n".join(lines)


def mix_reply(cfg: Config, text: str) -> str:
    """Antwort auf "/mix <key> <bpm> [toleranz]" (auch "/mix@botname …").
    Wörter nach dem Befehl: genau 2 oder 3, sonst MIX_USAGE.
    Key ungültig (compatible_keys wirft ValueError), BPM oder Toleranz keine Zahl, BPM außerhalb 60–250
    oder Toleranz nicht in (0, 20] -> MIX_USAGE. Komma als Dezimaltrenner erlaubt ("157,5"). Toleranz Standard 3.0.
    Sonst: TrackDB(cfg["state"]["track_db_path"]) öffnen,
    find_mix_candidates(db, key, bpm, bpm_tolerance=tol, limit=30), Ergebnis von
    format_mix_list(key, bpm, recs, bpm_tolerance=tol) mit html.escape(..., quote=False) zurückgeben
    (der Bot sendet mit parse_mode HTML). Fehler beim DB-Zugriff werden NICHT hier abgefangen."""
    parts = text.strip().split()
    if not parts or parts[0].split("@")[0].lower() != "/mix":
        return MIX_USAGE
    args = parts[1:]
    if len(args) not in (2, 3):
        return MIX_USAGE

    try:
        compatible_keys(args[0])
    except ValueError:
        return MIX_USAGE
    key = args[0]

    try:
        bpm = float(args[1].replace(",", "."))
    except ValueError:
        return MIX_USAGE
    if not (60.0 <= bpm <= 250.0):
        return MIX_USAGE

    if len(args) == 3:
        try:
            tol = float(args[2].replace(",", "."))
        except ValueError:
            return MIX_USAGE
        if not (0.0 < tol <= 20.0):
            return MIX_USAGE
    else:
        tol = 3.0

    track_db_path = cfg["state"]["track_db_path"]
    with TrackDB(track_db_path) as db:
        recs = find_mix_candidates(db, key, bpm, bpm_tolerance=tol, limit=30)
    result = format_mix_list(key, bpm, recs, bpm_tolerance=tol)
    return html.escape(result, quote=False)


def handle_message(cfg: Config, sc: SoundCloudClient, chat_id: str, text: str) -> None:
    text = text.strip()
    if text in ("/start", "/help"):
        _send_text(cfg, chat_id, HELP_TEXT)
        return

    first_word = text.split()[0].split("@")[0].lower() if text else ""
    if first_word == "/kaufliste":
        handle_kaufliste_command(cfg, chat_id=chat_id)
        return

    if first_word in ("/curator-mining", "/curator_mining"):
        try:
            msg = run_curator_mining(sc, cfg)
            _send_text(cfg, chat_id, msg)
        except Exception:
            log.exception("Fehler bei Curator-Mining")
            _send_text(cfg, chat_id, "Curator-Mining fehlgeschlagen, siehe Container-Log.")
        return

    if first_word == "/mix":
        try:
            _send_text(cfg, chat_id, mix_reply(cfg, text))
        except Exception:
            log.exception("Fehler bei Mix-Suche")
            _send_text(cfg, chat_id, "Mix-Suche fehlgeschlagen, siehe Container-Log.")
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
