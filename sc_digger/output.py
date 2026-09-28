"""Downloader (nur native Downloads), State-DB und Telegram-Digest."""
from __future__ import annotations

import html
import logging
import re
import sqlite3
import subprocess
from collections import defaultdict
from pathlib import Path

import requests

from .models import Config, DownloadKind, Track

log = logging.getLogger(__name__)


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
    """Alle Audio-Dateien unterhalb von *root* (rekursiv)."""
    return {p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in _AUDIO_EXTS}


def download_native(t: Track, inbox: Path) -> Path | None:
    """Lädt den vom Uploader freigegebenen Original-Download über scdl.

    Es werden ausschließlich Tracks mit downloadable=true geladen. Kein Gate-Handling.
    """
    inbox.mkdir(parents=True, exist_ok=True)
    before = _audio_files(inbox)
    # Kein --onlymp3: das würde die Original-Datei ausschließen.
    r = subprocess.run(
        ["scdl", "-l", t.url, "--path", str(inbox), "--original-art"],
        capture_output=True, text=True, timeout=300,
    )
    if r.returncode != 0:
        log.warning("scdl fehlgeschlagen für %s: %s", t.url, r.stderr[-300:])
        return None
    new = sorted(_audio_files(inbox) - before, key=lambda p: p.stat().st_mtime, reverse=True)
    return new[0] if new else None


# ---------------------------------------------------------------- Telegram
def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def _fmt_track(t: Track) -> str:
    line = (
        f'<a href="{html.escape(t.url)}">{_esc(t.artist)} – {_esc(t.title)}</a>\n'
        f"  ▶ {t.plays:,} · ♥ {t.likes:,} ({t.like_ratio * 100:.1f} %) · 🔁 {t.reposts:,}"
    )
    if t.bpm:
        line += f" · {t.bpm:.0f} BPM"
    if t.key_camelot:
        line += f" · 🔑 {t.key_camelot}"
    if t.quality_report:
        q = t.quality_report
        mark = "✅" if q["ok"] else "⚠️"
        line += f"\n  {mark} {q['ext'].upper()} {q['bitrate_kbps']} kbps – {_esc(q['reason'])}"
    if t.download_link and t.download_kind not in (DownloadKind.NATIVE, DownloadKind.NONE):
        line += f'\n  🔗 <a href="{html.escape(t.download_link)}">{t.download_kind.value}</a>'
    return line


def build_digest(tracks: list[Track], max_items: int | None, header: str | None = None) -> list[str]:
    """Baut Telegram-Nachrichten (max. 4096 Zeichen je Nachricht).

    max_items=None zeigt alle Tracks (für Playlist-Prüfungen, wo nichts verschwinden darf).
    """
    shown = tracks if max_items is None else tracks[:max_items]
    groups: dict[str, list[Track]] = defaultdict(list)
    for t in shown:
        k = t.download_kind
        if k == DownloadKind.NATIVE:
            groups["✅ Direkt geladen / ladbar"].append(t)
        elif k in (DownloadKind.HYPEDDIT, DownloadKind.DROPLOUD, DownloadKind.TONEDEN, DownloadKind.ARTIST_UNION):
            groups["🚪 Gate – manuell durchklicken"].append(t)
        elif k in (DownloadKind.STORE, DownloadKind.CLOUD):
            groups["🛒 Store / Cloud-Link"].append(t)
        else:
            groups["🎧 Nur Stream"].append(t)

    order = ["✅ Direkt geladen / ladbar", "🚪 Gate – manuell durchklicken",
             "🛒 Store / Cloud-Link", "🎧 Nur Stream"]
    messages: list[str] = []
    cur = f"<b>{_esc(header or f'sc-digger – {len(tracks)} neue Treffer')}</b>\n"
    for title in order:
        if title not in groups:
            continue
        group_head = f"\n<b>{title}</b>\n"
        # Wenn schon der Gruppen-Header die Nachricht sprengen würde → neue Nachricht
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


def send_telegram(cfg: Config, messages: list[str]) -> None:
    token, chat = cfg.telegram_token, cfg.telegram_chat_id
    if not token or not chat:
        log.warning("TELEGRAM_BOT_TOKEN/CHAT_ID fehlen – Digest wird nur geloggt")
        for m in messages:
            print(re.sub(r"<[^>]+>", "", m))
        return
    for m in messages:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": m, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=20,
        )
        r.raise_for_status()
