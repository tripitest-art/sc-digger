"""Downloader (nur Original-Dateien), Qualitäts-Abschluss, State-DB, Telegram-Digest und Export-Datei."""
from __future__ import annotations

import html
import logging
import re
import shutil
import sqlite3
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .models import Config, DownloadKind, Track
from .quality import check_file
from .redact import redact

log = logging.getLogger(__name__)


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
        return path
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
        line += f'\n  🔗 <a href="{html.escape(t.download_link)}">{t.download_kind.value}</a>'
    if t.download_error:
        line += f"\n  ⚠️ {_esc(t.download_error)}"
    return line


def build_digest(tracks: list[Track], max_items: int | None, header: str | None = None) -> list[str]:
    """Baut Telegram-Nachrichten (max. 4096 Zeichen je Nachricht).

    max_items=None zeigt alle Tracks (für Playlist-Prüfungen, wo nichts verschwinden darf).
    Umbrochen wird pro Track, damit keine einzelne Nachricht das Telegram-Limit sprengt.
    """
    shown = tracks if max_items is None else tracks[:max_items]
    groups: dict[str, list[Track]] = defaultdict(list)
    for t in shown:
        groups[_BUCKET_LABEL[t.download_kind]].append(t)

    messages: list[str] = []
    cur = f"<b>{_esc(header or f'sc-digger – {len(tracks)} neue Treffer')}</b>\n"
    for title in _ORDER:
        if title not in groups:
            continue
        group_head = f"\n<b>{title}</b>\n"
        if len(cur) + len(group_head) > 3900 and cur.strip():
            messages.append(cur)
            cur = ""
        cur += group_head
        for t in groups[title]:
            entry = _fmt_track(t) + "\n\n"
            if len(cur) + len(entry) > 3900:
                messages.append(cur)
                cur = f"<b>{title}</b> (Fortsetzung)\n"
            cur += entry
    if max_items is not None and len(tracks) > max_items:
        note = f"\n<i>… und {len(tracks) - max_items} weitere (max_items_per_digest={max_items})</i>\n"
        if len(cur) + len(note) > 3900:
            messages.append(cur)
            cur = ""
        cur += note
    if cur.strip():
        messages.append(cur)
    return messages


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
