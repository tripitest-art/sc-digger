"""Verfügbarkeits-Wächter für gelikte SoundCloud-Tracks.

Prüft regelmäßig (Wochen-Job, Sonntag 19:00 Uhr) mit 👍 bewertete Stream-Tracks
auf nachträglich hinzugefügte Download-Wege (Free Downloads, Gates, Cloud- oder Store-Links)
und meldet neu verfügbare Tracks per Telegram.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import html
import logging
from pathlib import Path
from typing import Callable

from .db import TrackDB
from .health import Health
from .models import Config, DownloadKind, Track
from .output import send_telegram
from .pipeline import classify_download
from .redact import install_redacting_logging, redact
from .soundcloud import SoundCloudClient

log = logging.getLogger("sc_digger.watch")

WATCH_MODE = "availability_watch"
DEFAULT_MAX_AGE_DAYS = 180


@dataclass(frozen=True)
class AvailabilityChange:
    sc_id: int
    title: str
    artist: str
    url: str  # SoundCloud-URL des Tracks
    kind: DownloadKind  # neuer Download-Weg, nie NONE
    link: str  # Download-, Gate-, Cloud- oder Store-Link (bei NATIVE die Track-URL)


def get_candidate_ids(
    db: TrackDB, *, now: datetime, max_age_days: int = DEFAULT_MAX_AGE_DAYS
) -> list[int]:
    """Ermittelt alle sc_ids von 👍-Tracks, die erneut geprüft werden sollen.

    Kandidaten (aufsteigend nach sc_id):
      - Feedback 'like'
      - nicht schon heruntergeladen (keine Zeile in tracks mit dieser sc_id)
      - in availability_watch entweder ohne Zeile oder mit available_at IS NULL und
        first_checked höchstens max_age_days vor now.
    """
    cutoff_str = (now - timedelta(days=max_age_days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = db.db.execute(
        """
        SELECT f.sc_id
        FROM sc_feedback f
        WHERE f.value = 'like'
          AND f.sc_id NOT IN (SELECT sc_id FROM tracks WHERE sc_id IS NOT NULL)
          AND (
              f.sc_id NOT IN (SELECT sc_id FROM availability_watch)
              OR f.sc_id IN (
                  SELECT sc_id FROM availability_watch
                  WHERE available_at IS NULL AND first_checked >= ?
              )
          )
        ORDER BY f.sc_id ASC
        """,
        (cutoff_str,),
    ).fetchall()
    return [int(row[0]) for row in rows]


def check_availability(
    db: TrackDB,
    fetch: Callable[[list[int]], list[Track]],
    *,
    now: datetime,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    dry_run: bool = False,
) -> list[AvailabilityChange]:
    """Prüft alle 👍-Tracks und liefert die neu verfügbaren.

    Wirft fetch, wird der Fehler weitergereicht und nichts in die DB geschrieben.
    """
    candidates = get_candidate_ids(db, now=now, max_age_days=max_age_days)
    if not candidates:
        return []

    fetched = fetch(candidates)
    track_map = {t.id: t for t in fetched}

    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    placeholders = ",".join("?" for _ in candidates)
    rows = db.db.execute(
        f"SELECT sc_id, first_checked, last_kind, available_at FROM availability_watch WHERE sc_id IN ({placeholders})",
        candidates,
    ).fetchall()
    watch_rows = {row[0]: dict(row) for row in rows}

    changes: list[AvailabilityChange] = []

    for sc_id in candidates:
        track = track_map.get(sc_id)
        existing = watch_rows.get(sc_id)

        if track is None:
            # von fetch nicht geliefert (gelöscht/privat)
            if existing is None:
                if not dry_run:
                    db.db.execute(
                        "INSERT INTO availability_watch (sc_id, first_checked, last_checked, last_kind, available_at) "
                        "VALUES (?, ?, ?, ?, NULL)",
                        (sc_id, now_str, now_str, DownloadKind.NONE.value),
                    )
            else:
                if not dry_run:
                    db.db.execute(
                        "UPDATE availability_watch SET last_checked = ? WHERE sc_id = ?",
                        (now_str, sc_id),
                    )
            continue

        classify_download(track)
        new_kind = track.download_kind
        is_available = new_kind != DownloadKind.NONE

        if existing is None:
            # Erste Prüfung: Zeile anlegen. Ist der Track schon verfügbar, available_at setzen, aber nicht melden.
            available_at = now_str if is_available else None
            if not dry_run:
                db.db.execute(
                    "INSERT INTO availability_watch (sc_id, first_checked, last_checked, last_kind, available_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (sc_id, now_str, now_str, new_kind.value, available_at),
                )
        else:
            last_kind = existing["last_kind"]
            if last_kind == DownloadKind.NONE.value and is_available:
                # Wechsel von stream auf Download-Weg: melden
                if not dry_run:
                    db.db.execute(
                        "UPDATE availability_watch SET last_checked = ?, last_kind = ?, available_at = ? WHERE sc_id = ?",
                        (now_str, new_kind.value, now_str, sc_id),
                    )
                changes.append(
                    AvailabilityChange(
                        sc_id=sc_id,
                        title=track.title,
                        artist=track.artist,
                        url=track.url,
                        kind=new_kind,
                        link=track.download_link or track.url,
                    )
                )
            else:
                if not dry_run:
                    db.db.execute(
                        "UPDATE availability_watch SET last_checked = ?, last_kind = ? WHERE sc_id = ?",
                        (now_str, new_kind.value, sc_id),
                    )

    if not dry_run:
        db.db.commit()

    return changes


def format_available_message(changes: list[AvailabilityChange]) -> list[str]:
    """Formatiert gefundene Verfügbarkeits-Änderungen als Telegram-HTML-Nachrichten.

    Gibt [] zurück bei leerer Liste. Sonst Nachrichten bis max. 4000 Zeichen.
    """
    if not changes:
        return []

    header = "🔓 <b>Jetzt verfügbar</b>\n\n"
    messages: list[str] = []
    current_lines = [header]
    current_len = len(header)

    for i, c in enumerate(changes):
        item_text = (
            f"• {html.escape(c.artist)} – {html.escape(c.title)}\n"
            f"   → {html.escape(c.kind.value)}: {html.escape(c.link)}"
        )
        sep = "\n\n" if len(current_lines) > 1 else ""
        add_len = len(sep) + len(item_text)

        if current_len + add_len > 4000 and len(current_lines) > 1:
            messages.append("".join(current_lines).strip())
            current_lines = [item_text]
            current_len = len(item_text)
        else:
            if len(current_lines) > 1:
                current_lines.append(sep)
            current_lines.append(item_text)
            current_len += add_len

    if current_lines:
        messages.append("".join(current_lines).strip())

    return messages


def run_watch(
    cfg: Config,
    *,
    fetch: Callable[[list[int]], list[Track]] | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> int:
    """Führt den wöchentlichen Verfügbarkeits-Wächter aus.

    Exit-Codes:
    0: Erfolg (oder deaktiviert)
    1: Fehler während der Prüfung (Health-Alarm protokolliert, Telegram alarmiert)
    """
    watch_cfg = cfg.raw.get("watch") or {}
    if not watch_cfg.get("enabled", True):
        return 0

    if now is None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)

    max_age_days = int(watch_cfg.get("max_age_days") or DEFAULT_MAX_AGE_DAYS)
    if fetch is None:
        fetch = SoundCloudClient().tracks_by_ids

    state_cfg = cfg.raw.get("state") or {}
    track_db_path = Path(state_cfg.get("track_db_path", "data/tracks.sqlite"))
    seen_db_path = Path(state_cfg.get("db_path", "data/seen.sqlite"))

    try:
        with TrackDB(track_db_path) as db:
            candidates = get_candidate_ids(db, now=now, max_age_days=max_age_days)
            candidate_count = len(candidates)
            changes = check_availability(
                db, fetch, now=now, max_age_days=max_age_days, dry_run=dry_run
            )

        with Health(seen_db_path) as h:
            h.record(WATCH_MODE, ok=True, found=candidate_count)

        if changes:
            msgs = format_available_message(changes)
            if dry_run:
                for m in msgs:
                    print(m)
            else:
                send_telegram(cfg, msgs)

        return 0
    except Exception as exc:
        log.exception("Verfügbarkeits-Wächter fehlgeschlagen: %s", exc)
        try:
            with Health(seen_db_path) as h:
                h.record(WATCH_MODE, ok=False, found=0, error=str(exc))
        except Exception as he:
            log.warning("Health-Protokollierung fehlgeschlagen: %s", he)

        err_msg = (
            f"⚠️ <b>Verfügbarkeits-Wächter fehlgeschlagen</b>: "
            f"{html.escape(redact(str(exc)))}"
        )
        if dry_run:
            print(err_msg)
        else:
            try:
                send_telegram(cfg, [err_msg])
            except Exception as te:
                log.warning("Telegram-Alarmierung fehlgeschlagen: %s", te)

        return 1


def main(argv: list[str] | None = None) -> int:
    """CLI-Einstiegspunkt für python -m sc_digger.watch."""
    install_redacting_logging(logging.INFO)

    parser = argparse.ArgumentParser(
        description="Verfügbarkeits-Wächter für gelikte SoundCloud-Tracks"
    )
    parser.add_argument(
        "--config", default="config.yaml", help="Pfad zur Konfigurationsdatei"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur simulieren, keine DB-Schreibzugriffe und keine Telegram-Nachrichten",
    )
    args = parser.parse_args(argv)

    cfg = Config.load(args.config)
    return run_watch(cfg, dry_run=args.dry_run)


if __name__ == "__main__":
    import sys

    sys.exit(main())
