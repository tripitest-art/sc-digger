"""Downloader (nur Original-Dateien), Qualitäts-Abschluss, State-DB, Telegram-Digest und Export-Datei."""
from __future__ import annotations

import html
import logging
import re
import shutil
import sqlite3
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from .db import TrackDB
from .fingerprint import compute_fingerprint, find_same_recording
from .loudness import normalize_inbox_file
from .models import Config, DownloadKind, Track
from .quality import check_file
from .redact import redact

log = logging.getLogger(__name__)


@dataclass
class DigestMessage:
    text: str
    items: list[tuple[int, int]] = field(default_factory=list)


class TelegramError(RuntimeError):
    """Telegram-Fehler ohne Token in der Meldung (anders als requests.HTTPError)."""


def telegram_call(token: str, method: str, *, http: str = "post", timeout: int = 20, **kwargs) -> dict:
    """Ruft die Telegram-API auf. Fehler kommen als TelegramError mit Telegrams Beschreibung,
    nie mit der URL, weil die den Bot-Token enthält."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        r = getattr(requests, http)(url, timeout=timeout, **kwargs)
    except requests.RequestException as e:
        raise TelegramError(redact(f"{method}: {type(e).__name__}: {e}")) from None
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400 or not body.get("ok", False):
        raise TelegramError(f"{method}: Telegram {r.status_code}: {body.get('description', 'unbekannter Fehler')}")
    return body


# ---------------------------------------------------------------- State
class State:
    """Merkt sich gemeldete Tracks, damit der Digest nie zweimal dasselbe zeigt."""

    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path))
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS seen ("
            "id INTEGER PRIMARY KEY, url TEXT, title TEXT, "
            "reported_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        self.db.commit()

    def __enter__(self) -> "State":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.db.close()

    def is_seen(self, track_id: int) -> bool:
        return self.db.execute("SELECT 1 FROM seen WHERE id=?", (track_id,)).fetchone() is not None

    def mark_one(self, t: Track) -> None:
        """Markiert einen einzelnen Track als gesendet."""
        self.db.execute(
            "INSERT OR IGNORE INTO seen (id, url, title) VALUES (?,?,?)",
            (t.id, t.url, t.title),
        )
        self.db.commit()

    def mark(self, tracks: list[Track]) -> None:
        self.db.executemany(
            "INSERT OR IGNORE INTO seen (id, url, title) VALUES (?,?,?)",
            [(t.id, t.url, t.title) for t in tracks],
        )
        self.db.commit()


# ---------------------------------------------------------------- Download
_AUDIO_EXTS = {".wav", ".aiff", ".aif", ".flac", ".mp3", ".m4a"}


def _audio_files(root: Path) -> set[Path]:
    """Alle Audio-Dateien unterhalb von *root* (rekursiv, Inbox hat BPM/Key-Unterordner)."""
    return {p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in _AUDIO_EXTS}


def download_native(t: Track, inbox: Path, auth_token: str | None = None) -> Path | None:
    """Lädt die vom Uploader freigegebene Original-Datei über scdl.

    --only-original verhindert, dass scdl still auf den Stream (AAC/MP3, 128-160 kbps)
    ausweicht. Originale liefert SoundCloud nur mit Login, deshalb der auth_token
    (OAuth-Token des eigenen Accounts, SOUNDCLOUD_AUTH_TOKEN in der .env).
    Ohne Token wird gar nicht erst versucht; der Digest verlinkt dann zum manuellen Laden.

    Achtung: scdl beendet sich auch bei "format not available" mit Exit-Code 0.
    Erfolg wird deshalb daran gemessen, ob eine neue Datei entstanden ist.
    """
    if not auth_token:
        t.notes.append("Original nur mit SoundCloud-Login (SOUNDCLOUD_AUTH_TOKEN fehlt)")
        return None
    inbox.mkdir(parents=True, exist_ok=True)
    before = _audio_files(inbox)
    try:
        r = subprocess.run(
            ["scdl", "-l", t.url, "--path", str(inbox), "--only-original", "--original-art",
             "--auth-token", auth_token],
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        # Die Exception-Meldung enthält die Kommandozeile samt Token -> nicht weiterreichen
        log.warning("scdl-Timeout für %s", t.url)
        t.notes.append("Original-Download fehlgeschlagen (Timeout)")
        return None
    new = sorted(_audio_files(inbox) - before, key=lambda p: p.stat().st_mtime, reverse=True)
    if not new:
        tail = (r.stderr or r.stdout or "")[-300:].replace(auth_token, "***")
        log.warning("Kein Original geladen für %s: %s", t.url, tail)
        t.notes.append("Original-Download fehlgeschlagen")
        return None
    return new[0]


def reject_if_duplicate(t: Track, path: Path, inbox: Path, cfg: Config) -> Path | None:
    """cfg.raw.get("fingerprint", {}).get("check_downloads", True) falsch -> path (ohne fpcalc).
    fp = compute_fingerprint(path); None -> path.
    with TrackDB(cfg["state"]["track_db_path"]) as db: rec = find_same_recording(fp, db)
    Kein Treffer -> path.
    Treffer -> Datei nach inbox/"_rejected"/"duplicate"/<gleicher Dateiname> verschieben
    (Ordner anlegen), t.duplicate_of = rec.path, Rückgabe None.
    Jede Exception: log.warning mit "Duplikat" im Text, Rückgabe path (im Zweifel behalten, nie werfen)."""
    if not (cfg.raw.get("fingerprint") or {}).get("check_downloads", True):
        return path
    try:
        fp = compute_fingerprint(path)
        if fp is None:
            return path
        with TrackDB(cfg["state"]["track_db_path"]) as db:
            rec = find_same_recording(fp, db)
        if rec is None:
            return path
        target = inbox / "_rejected" / "duplicate"
        target.mkdir(parents=True, exist_ok=True)
        dest = target / path.name
        if dest.exists():
            dest.unlink()
        shutil.move(str(path), str(dest))
        t.duplicate_of = rec.path
        return None
    except Exception as e:
        log.warning("Duplikat-Prüfung fehlgeschlagen: %s", e)
        return path


def finalize_quality(t: Track, path: Path, inbox: Path, cfg: Config) -> Path | None:
    """Prüft eine geladene Datei und räumt sie ggf. weg. Von main.py und bot.py geteilt.

    Zwei getrennte Ablehnungsgründe, zwei getrennte Ordner: _rejected/ für Fake-Bitrate
    (Codec/Spektrum stimmt nicht), _rejected/clipped/ für Brickwall-Mastering (Datei ist
    technisch echt, aber vom Pegel her unbrauchbar).

    Gibt den Pfad zurück, wenn die Datei in der Inbox bleibt, sonst None.
    """
    try:
        t.quality_report = check_file(path, cfg)
    except Exception as e:
        t.notes.append(f"Qualitätsprüfung fehlgeschlagen: {e}")
        return None
    if not t.quality_report["ok"]:
        target = inbox / "_rejected"
    elif t.quality_report.get("clipped"):
        target = inbox / "_rejected" / "clipped"
    else:
        res = reject_if_duplicate(t, path, inbox, cfg)
        if res is not None:
            normalize_inbox_file(res, t.quality_report, cfg)
        return res
    target.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(target / path.name))
    return None


# ---------------------------------------------------------------- Telegram-Chat (HTML, kompakt)
_BUCKET_LABEL = {
    DownloadKind.NATIVE: "✅ Direkt geladen / ladbar",
    DownloadKind.HYPEDDIT: "🚪 Gate – manuell durchklicken",
    DownloadKind.DROPLOUD: "🚪 Gate – manuell durchklicken",
    DownloadKind.TONEDEN: "🚪 Gate – manuell durchklicken",
    DownloadKind.ARTIST_UNION: "🚪 Gate – manuell durchklicken",
    DownloadKind.STORE: "🛒 Store / Cloud-Link",
    DownloadKind.CLOUD: "🛒 Store / Cloud-Link",
    DownloadKind.WETRANSFER: "🛒 Store / Cloud-Link",
    DownloadKind.MEGA: "🛒 Store / Cloud-Link",
    DownloadKind.NONE: "🎧 Nur Stream",
}
_ORDER = ["✅ Direkt geladen / ladbar", "🚪 Gate – manuell durchklicken",
          "🛒 Store / Cloud-Link", "🎧 Nur Stream"]


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def _fmt_track(t: Track) -> str:
    prefix = "⭐ " if t.reference_hit else ""
    line = (
        f'{prefix}<a href="{html.escape(t.url)}">{_esc(t.artist)} – {_esc(t.title)}</a>\n'
        f"  ▶ {t.plays:,} · ♥ {t.likes:,} ({t.like_ratio * 100:.1f} %) · 🔁 {t.reposts:,}"
    )
    if t.bpm:
        line += f" · {t.bpm:.0f} BPM"
    if t.key_camelot:
        line += f" · 🔑 {t.key_camelot}"
    if t.set_minutes:
        line += f" · 🎛️ Set, {t.set_minutes} min"
    if t.exploration_tag:
        line += f"\n  🔍 via #{_esc(t.exploration_tag)}"
    if t.quality_report:
        q = t.quality_report
        mark = "✅" if q["ok"] else "⚠️"
        line += f"\n  {mark} {q['ext'].upper()} {q['bitrate_kbps']} kbps – {_esc(q['reason'])}"
        if q.get("clipped"):
            line += (f"\n  🧱 Brickwall/Clipping (LRA: {q.get('loudness_range_lu')} LU, "
                     f"Peak: {q.get('true_peak_dbfs')} dBFS)")
    elif t.download_kind == DownloadKind.NATIVE:
        line += f'\n  ⬇️ <a href="{html.escape(t.url)}">Original manuell laden</a>'
    if t.download_link and t.download_kind not in (DownloadKind.NATIVE, DownloadKind.NONE):
        if t.download_kind == DownloadKind.WETRANSFER:
            line += f'\n  ⏳ <a href="{html.escape(t.download_link)}">WeTransfer (läuft ab)</a>'
        elif t.download_kind == DownloadKind.MEGA:
            line += f'\n  🔒 <a href="{html.escape(t.download_link)}">Mega (manuell)</a>'
        else:
            line += f'\n  🔗 <a href="{html.escape(t.download_link)}">{t.download_kind.value}</a>'
    if t.download_error:
        line += f"\n  ⚠️ {_esc(t.download_error)}"
    if t.duplicate_of:
        line += f"\n  ♻️ Schon in der Sammlung: {_esc(Path(t.duplicate_of).name)}"
    return line


_FB_PATTERN = re.compile(r"^fb:(like|dislike|later):(\d+)$")


def feedback_callback_data(value: str, sc_id: int) -> str:
    """Erzeugt Telegram-Callback-Data im Format 'fb:<value>:<sc_id>' (max. 64 Byte)."""
    return f"fb:{value}:{sc_id}"


def parse_feedback_callback(data: str | None) -> tuple[str, int] | None:
    """Genau 'fb:(like|dislike|later):<nur Ziffern>' -> (value, sc_id), alles andere None. Wirft nie."""
    if not data or not isinstance(data, str):
        return None
    m = _FB_PATTERN.match(data)
    if not m:
        return None
    try:
        return m.group(1), int(m.group(2))
    except (ValueError, TypeError):
        return None


def feedback_keyboard(items: list[tuple[int, int]]) -> dict | None:
    """{"inline_keyboard": [[{"text": "<n> 👍", "callback_data": ...like...},
                                {"text": "<n> 👎", ...dislike...}, {"text": "<n> ⏳", ...later...}], ...]}
    eine Reihe pro Eintrag in items, Reihenfolge wie items. Leere Liste -> None."""
    if not items:
        return None
    rows = []
    for n, sc_id in items:
        rows.append([
            {"text": f"{n} 👍", "callback_data": feedback_callback_data("like", sc_id)},
            {"text": f"{n} 👎", "callback_data": feedback_callback_data("dislike", sc_id)},
            {"text": f"{n} ⏳", "callback_data": feedback_callback_data("later", sc_id)},
        ])
    return {"inline_keyboard": rows}


def build_digest_messages(
    tracks: list[Track],
    max_items: int | None = None,
    header: str | None = None,
    *,
    numbered: bool = False,
    cfg: Config | None = None,
) -> list[DigestMessage]:
    """Heutige Logik von build_digest (Gruppen, Umbruch bei 3900 Zeichen, Fortsetzungs-Kopf,
    „… und N weitere“), zusätzlich:
    numbered=True: jeder Track-Eintrag beginnt mit "<b>{n}.</b> "; n zählt ab 1 in Anzeige-
    reihenfolge über alle Nachrichten; jede Nachricht enthält höchstens 10 Tracks (sonst neue
    Nachricht mit Fortsetzungs-Kopf); items enthält (n, t.id) der Tracks dieser Nachricht.
    numbered=False: Text exakt wie bisher, items leer.
    Store-Tracks, die in den Nachrichten erscheinen, per upsert_store_item in store_items
    eintragen (Track-DB aus cfg["state"]["track_db_path"]).
    cfg None: Config.load("config.yaml"). Scheitert das Laden oder das Eintragen: log.warning,
    Digest wird trotzdem gebaut."""
    shown = tracks if max_items is None else tracks[:max_items]

    store_tracks = [t for t in shown if t.download_kind == DownloadKind.STORE and t.purchase_url]
    if store_tracks:
        cfg_to_use = cfg
        if cfg_to_use is None:
            from .models import Config
            try:
                cfg_to_use = Config.load("config.yaml")
            except Exception as e:
                log.warning("Konnte config.yaml für Store-Items nicht laden: %s", e)
                cfg_to_use = None

        if cfg_to_use is not None and "state" in cfg_to_use.raw and "track_db_path" in cfg_to_use["state"]:
            try:
                from .db import TrackDB
                with TrackDB(cfg_to_use["state"]["track_db_path"]) as db:
                    for t in store_tracks:
                        db.upsert_store_item(t.id, t.title, t.artist, t.purchase_url, t.purchase_title)
            except Exception as e:
                log.warning("Konnte Store-Tracks nicht in DB eintragen: %s", e)
    groups: dict[str, list[Track]] = defaultdict(list)
    for t in shown:
        groups[_BUCKET_LABEL[t.download_kind]].append(t)

    messages: list[DigestMessage] = []
    cur = f"<b>{_esc(header or f'sc-digger – {len(tracks)} neue Treffer')}</b>\n"
    cur_items: list[tuple[int, int]] = []
    n = 1

    for title in _ORDER:
        if title not in groups:
            continue
        group_head = f"\n<b>{title}</b>\n"
        if (len(cur) + len(group_head) > 3900 or (numbered and len(cur_items) >= 10)) and cur.strip():
            messages.append(DigestMessage(text=cur, items=cur_items))
            cur = ""
            cur_items = []
        cur += group_head
        for t in groups[title]:
            prefix = f"<b>{n}.</b> " if numbered else ""
            entry = prefix + _fmt_track(t) + "\n\n"
            if (len(cur) + len(entry) > 3900) or (numbered and len(cur_items) >= 10):
                if cur.strip():
                    messages.append(DigestMessage(text=cur, items=cur_items))
                cur = f"<b>{title}</b> (Fortsetzung)\n"
                cur_items = []
            cur += entry
            if numbered:
                cur_items.append((n, t.id))
                n += 1
    if max_items is not None and len(tracks) > max_items:
        note = f"\n<i>… und {len(tracks) - max_items} weitere (max_items_per_digest={max_items})</i>\n"
        if len(cur) + len(note) > 3900:
            if cur.strip():
                messages.append(DigestMessage(text=cur, items=cur_items))
            cur = ""
            cur_items = []
        cur += note
    if cur.strip():
        messages.append(DigestMessage(text=cur, items=cur_items))
    return messages


def build_digest(tracks: list[Track], max_items: int | None, header: str | None = None) -> list[str]:
    """Unverändertes Verhalten: [m.text for m in build_digest_messages(..., numbered=False)]"""
    return [m.text for m in build_digest_messages(tracks, max_items, header=header, numbered=False)]


def send_digest(
    cfg: Config,
    messages: list[DigestMessage],
    chat_id: str | None = None,
    *,
    buttons: bool,
) -> None:
    """Wie send_telegram (sendMessage über telegram_call, parse_mode HTML, ohne Link-Vorschau,
    fehlende Zugangsdaten -> nur loggen und drucken). Bei buttons=True und nicht leeren items
    zusätzlich "reply_markup": feedback_keyboard(items)."""
    token, chat = cfg.telegram_token, chat_id or cfg.telegram_chat_id
    if not token or not chat:
        log.warning("TELEGRAM_BOT_TOKEN/CHAT_ID fehlen – Digest wird nur geloggt")
        for m in messages:
            print(re.sub(r"<[^>]+>", "", m.text))
        return
    for m in messages:
        payload: dict[str, Any] = {
            "chat_id": chat,
            "text": m.text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if buttons and m.items:
            kb = feedback_keyboard(m.items)
            if kb:
                payload["reply_markup"] = kb
        telegram_call(token, "sendMessage", json=payload)


def send_telegram(cfg: Config, messages: list[str], chat_id: str | None = None) -> None:
    token, chat = cfg.telegram_token, chat_id or cfg.telegram_chat_id
    if not token or not chat:
        log.warning("TELEGRAM_BOT_TOKEN/CHAT_ID fehlen – Digest wird nur geloggt")
        for m in messages:
            print(re.sub(r"<[^>]+>", "", m))
        return
    for m in messages:
        telegram_call(token, "sendMessage", json={"chat_id": chat, "text": m, "parse_mode": "HTML",
                                                  "disable_web_page_preview": True})


# ---------------------------------------------------------------- Export-Datei (Box-Stil, für Download-Tool)
_WIDTH = 81
_BAR = "═" * _WIDTH


def _center(text: str) -> str:
    return text.center(_WIDTH)


def _now_str() -> str:
    d = datetime.now(ZoneInfo("Europe/Berlin"))
    return f"{d.day}.{d.month}.{d.year}, {d:%H:%M:%S}"


def _fmt_track_txt(t: Track) -> str:
    """Ein Feld pro Track im Format [Genre]  |  Artist - Titel  |  URL (kompatibel zu Extension-Exports)."""
    tag = t.genre.strip() if t.genre and t.genre.strip() else "Unknown Genre"
    if t.reference_hit:
        tag = f"REF: {tag}"
    if t.quality_report and t.quality_report.get("clipped"):
        tag = f"CLIPPED: {tag}"
    link = t.download_link if (t.download_link and t.download_kind not in (DownloadKind.NATIVE, DownloadKind.NONE)) else t.url
    return f"[{tag}]  |  {t.artist} - {t.title}  |  {link}"


def build_export_txt(tracks: list[Track], folder_name: str = "sc-digger") -> str:
    """Baut die Export-Datei im Stil bekannter SoundCloud-Extension-Exporte, als Anhang zum Download-Tool."""
    groups: dict[str, list[Track]] = defaultdict(list)
    for t in tracks:
        groups[_BUCKET_LABEL[t.download_kind]].append(t)

    lines = [
        _BAR,
        _center("SOUNDCLOUD EXTENSION - SC-DIGGER EXPORT"),
        _BAR,
        "",
        "🎵 Generated by: sc-digger",
        f"📅 Export Date: {_now_str()}",
        f"📁 Folder Name: {folder_name}",
        "🌐 Website: https://soundcloud.com",
        "",
    ]
    for label in _ORDER:
        group = groups.get(label)
        if not group:
            continue
        lines += [_BAR, _center(f"FOLDER: {label.split(' ', 1)[1].upper()}"), _BAR, ""]
        lines += [_fmt_track_txt(t) for t in group]
        lines.append("")

    lines += [
        _BAR,
        _center("EXPORT SUMMARY"),
        _BAR,
        "",
        f"📊 Total Tracks Exported: {len(tracks)}",
        f"📁 Folder Name: {folder_name}",
        "🎵 Generated by: sc-digger",
        "",
        _BAR,
    ]
    return "\n".join(lines)


def send_telegram_document(cfg: Config, filename: str, content: str, chat_id: str | None = None) -> None:
    """Schickt die Export-Datei als herunterladbaren Anhang im Chat."""
    token, chat = cfg.telegram_token, chat_id or cfg.telegram_chat_id
    if not token or not chat:
        log.warning("TELEGRAM_BOT_TOKEN/CHAT_ID fehlen – Export-Datei wird nicht verschickt")
        return
    telegram_call(token, "sendDocument", timeout=30, data={"chat_id": chat},
                  files={"document": (filename, content.encode("utf-8"), "text/plain")})


def send_telegram_photo(
    cfg: Config,
    photo_bytes: bytes | None,
    caption: str | None = None,
    *,
    chat_id: str | None = None,
    filename: str = "stats.png",
) -> None:
    """Schickt ein PNG mit HTML-Caption an Telegram.

    Fehlen Token oder Chat, wird nur geloggt (wie bei send_telegram_document). Ohne
    photo_bytes geht nur der Text raus. Telegram-Grenze: Captions dürfen 1024 Zeichen
    nicht überschreiten, danach folgt der volle Text als eigene Nachricht. Ein Fehler
    beim Foto darf den Aufrufer nicht abbrechen: dann wird der Text nachgeschickt.
    """
    token, chat = cfg.telegram_token, chat_id or cfg.telegram_chat_id
    if not token or not chat:
        log.warning("TELEGRAM_BOT_TOKEN/CHAT_ID fehlen – Statistik-Foto wird nicht verschickt")
        return
    if photo_bytes:
        short = caption[:1024] if caption and len(caption) > 1024 else caption
        try:
            telegram_call(token, "sendPhoto", timeout=30,
                          data={"chat_id": chat, "caption": short, "parse_mode": "HTML"},
                          files={"photo": (filename, photo_bytes, "image/png")})
        except TelegramError as e:
            log.warning("sendPhoto fehlgeschlagen: %s", e)
            if caption:
                telegram_call(token, "sendMessage",
                              json={"chat_id": chat, "text": caption, "parse_mode": "HTML"})
            return
        if caption and len(caption) > 1024:
            telegram_call(token, "sendMessage",
                          json={"chat_id": chat, "text": caption, "parse_mode": "HTML"})
    elif caption:
        telegram_call(token, "sendMessage",
                      json={"chat_id": chat, "text": caption, "parse_mode": "HTML"})


def send_telegram_voice(
    cfg: Config,
    voice_path: Path,
    caption: str | None = None,
    *,
    chat_id: str | None = None,
) -> None:
    """Sendet eine Audiodatei als Voice-Message (Telegram-Methode sendVoice),
    nach dem Muster von send_telegram_photo: multipart-Versand mit
    data={"chat_id": ..., "caption": ...} und files={"voice":
    (name, filehandle, "audio/ogg")}. chat_id-Parameter überschreibt
    cfg.telegram_chat_id (für Tests).
    Ohne Token/Chat-ID: nur Log-Meldung, kein Versand.
    TelegramError wird NICHT abgefangen, sondern geworfen (Behandler:
    preview_reply)."""
    token, chat = cfg.telegram_token, chat_id or cfg.telegram_chat_id
    if not token or not chat:
        log.warning("TELEGRAM_BOT_TOKEN/CHAT_ID fehlen – Voice-Nachricht wird nicht verschickt")
        return
    path = Path(voice_path)
    with open(path, "rb") as fh:
        telegram_call(
            token,
            "sendVoice",
            timeout=60,
            data={"chat_id": chat, "caption": caption or ""},
            files={"voice": (path.name, fh, "audio/ogg")},
        )


def send_kaufliste(cfg: Config, *, dry_run: bool = False, chat_id: str | None = None) -> None:
    """Liest store_items aus der DB, formatiert als Telegram-Nachricht.
    Format pro Eintrag: "🛒 <Artist> – <Title>\n   → <purchase_title or 'Kaufen'>: <purchase_url>"
    Maximal cfg["digest"].get("kaufliste_max_items", 30) Einträge, neueste zuerst (last_seen DESC).
    Sendet via output.telegram_call(token, "sendMessage", json={...}), auch ohne Token
    (telegram_call meldet das als TelegramError, ohne Token im Text).
    Bei 0 Einträgen: kurze Meldung "Kaufliste ist leer."
    dry_run=True: nur auf stdout ausgeben, kein telegram_call."""
    from .db import TrackDB
    import html

    db_path = cfg["state"]["track_db_path"]
    with TrackDB(db_path) as db:
        items = db.get_store_items()

    limit = 30
    if "digest" in cfg.raw and isinstance(cfg.raw["digest"], dict):
        limit = cfg.raw["digest"].get("kaufliste_max_items", 30)
    items = items[:limit]

    if not items:
        chunks = ["Kaufliste ist leer."]
    else:
        entries = []
        for it in items:
            artist = html.escape(it.get("artist") or "")
            title = html.escape(it.get("title") or "")
            p_title = html.escape(it.get("purchase_title") or "Kaufen")
            url = html.escape(it.get("purchase_url") or "")
            entries.append(f"🛒 {artist} – {title}\n   → {p_title}: {url}")

        chunks = []
        current_chunk: list[str] = []
        current_len = 0
        MAX_MSG_LEN = 4000

        for entry in entries:
            added_len = len(entry) + (2 if current_chunk else 0)
            if current_chunk and (current_len + added_len > MAX_MSG_LEN):
                chunks.append("\n\n".join(current_chunk))
                current_chunk = [entry]
                current_len = len(entry)
            else:
                current_chunk.append(entry)
                current_len += added_len
        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

    if dry_run:
        for chunk in chunks:
            print(re.sub(r"<[^>]+>", "", chunk))
        return

    token = cfg.telegram_token or ""
    chat = chat_id or cfg.telegram_chat_id or ""

    for chunk in chunks:
        payload: dict[str, Any] = {
            "chat_id": chat,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        telegram_call(token, "sendMessage", json=payload)


def send_scout_error(
    cfg: Config,
    source: str,
    message: str,
    *,
    chat_id: str | None = None,
) -> None:
    """Meldet den Fehler einer Scout-Quelle per Telegram.

    Die Nachricht nennt die Quelle, damit im Digest erkennbar ist, welcher Scout
    ausgefallen ist. Fehler von telegram_call werden wie bei send_kaufliste nicht
    abgefangen; der Aufrufer entscheidet, ob er den Lauf fortsetzt.
    """
    token = cfg.telegram_token or ""
    chat = chat_id or cfg.telegram_chat_id or ""
    text = f"⚠️ Scout {source} fehlgeschlagen: {_esc(message)}"
    payload: dict[str, Any] = {
        "chat_id": chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    telegram_call(token, "sendMessage", json=payload)


def send_scout_digest(
    cfg: Config,
    items: list[dict[str, Any]],
    *,
    max_items: int = 5,
    chat_id: str | None = None,
) -> None:
    """Sendet den Digest-Block „🛒 Neu bei Bandcamp/Beatport".

    items sind die in diesem Lauf neu in store_items geschriebenen Einträge
    (neueste zuerst). Es werden höchstens max_items gezeigt. Bei leerer Liste
    wird nichts gesendet.
    """
    if not items:
        return

    entries = []
    for it in items[:max_items]:
        artist = _esc(it.get("artist") or "")
        title = _esc(it.get("title") or "")
        p_title = _esc(it.get("purchase_title") or "Kaufen")
        url = html.escape(it.get("purchase_url") or "")
        entries.append(f"🛒 {artist} – {title}\n   → {p_title}: {url}")

    text = "🛒 Neu bei Bandcamp/Beatport\n\n" + "\n\n".join(entries)
    token = cfg.telegram_token or ""
    chat = chat_id or cfg.telegram_chat_id or ""
    payload: dict[str, Any] = {
        "chat_id": chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    telegram_call(token, "sendMessage", json=payload)
