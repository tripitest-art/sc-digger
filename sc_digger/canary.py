"""API-Kanarienvogel für SoundCloud-Antworten.

Erkennt still geänderte oder weggefallene Felder in den inoffiziellen api-v2-Antworten
von SoundCloud vor dem täglichen discover-Lauf und schlägt bei Formatbrüchen Alarm.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Iterable

import requests

from .health import Health
from .models import Config
from .output import send_telegram
from .redact import install_redacting_logging
from .soundcloud import SoundCloudClient, SoundCloudError

log = logging.getLogger("sc_digger.canary")

TRACK_FIELDS: tuple[str, ...] = (
    "id",
    "kind",
    "title",
    "permalink_url",
    "created_at",
    "duration",
    "playback_count",
    "likes_count",
    "reposts_count",
    "comment_count",
    "downloadable",
    "tag_list",
    "description",
    "user.username",
    "user.permalink_url",
    "media.transcodings",
)


def missing_fields(obj: Any, fields: Iterable[str], prefix: str = "") -> list[str]:
    """Prüft, ob alle Punkt-Pfade in obj vorhanden sind.

    Ein Feld gilt als vorhanden, wenn jeder Schlüssel existiert (Wert darf None sein).
    Ist ein Zwischenwert kein dict -> fehlt.
    Rückgabe: prefix + Pfad je fehlendem Feld, in der Reihenfolge von fields.
    """
    missing: list[str] = []
    for field in fields:
        parts = field.split(".")
        curr = obj
        is_missing = False
        for part in parts:
            if not isinstance(curr, dict) or part not in curr:
                is_missing = True
                break
            curr = curr[part]
        if is_missing:
            missing.append(f"{prefix}{field}")
    return missing


def check_search(payload: Any) -> list[str]:
    """Prüft das Such-Antwortformat von SoundCloud.

    payload kein dict oder "collection" fehlt/kein list/leer -> ["search.collection"].
    Erster Eintrag mit kind == "track"; keiner -> ["search.collection[kind=track]"].
    Sonst missing_fields(eintrag, TRACK_FIELDS, "search[0].").
    """
    if not isinstance(payload, dict):
        return ["search.collection"]
    coll = payload.get("collection")
    if not isinstance(coll, list) or not coll:
        return ["search.collection"]
    track = next(
        (item for item in coll if isinstance(item, dict) and item.get("kind") == "track"),
        None,
    )
    if track is None:
        return ["search.collection[kind=track]"]
    return missing_fields(track, TRACK_FIELDS, "search[0].")


def check_track(payload: Any, label: str = "track") -> list[str]:
    """Prüft das Antwortformat eines einzelnen Tracks.

    Kein dict oder kind != "track" -> [f"{label}.kind"];
    sonst missing_fields(payload, TRACK_FIELDS, f"{label}.").
    """
    if not isinstance(payload, dict) or payload.get("kind") != "track":
        return [f"{label}.kind"]
    return missing_fields(payload, TRACK_FIELDS, f"{label}.")


def run_canary(
    sc: SoundCloudClient, *, tag: str, track_url: str | None = None
) -> list[str]:
    """Ruft die SoundCloud-API ab und prüft das Schema der Antworten.

    - check_search(sc._get("/search/tracks", {"q": tag, "limit": 5}))
    - plus, wenn track_url: check_track(sc._get("/resolve", {"url": track_url}))
    SoundCloudError und requests.RequestException werden durchgereicht.
    """
    search_resp = sc._get("/search/tracks", {"q": tag, "limit": 5})
    problems = list(check_search(search_resp))
    if track_url:
        track_resp = sc._get("/resolve", {"url": track_url})
        problems.extend(check_track(track_resp))
    return problems


def problems_text(problems: list[str]) -> str:
    """Formatiert die Liste fehlender Felder für den Health-Alarm."""
    return (
        f"SoundCloud-Antwort geändert, Felder fehlen: {', '.join(problems)} "
        "→ soundcloud.py (_to_track) prüfen"
    )


def record_canary(db_path: str | Path, problems: list[str]) -> str | None:
    """Trägt das Ergebnis in die Health-DB ein und liefert eine Alarm- oder Entwarnungsnachricht."""
    with Health(db_path) as h:
        if problems:
            h.record("api_schema", ok=False, found=0, error=problems_text(problems))
        else:
            h.record("api_schema", ok=True, found=1)
        return h.evaluate("api_schema", 1)


def main(argv: list[str] | None = None) -> int:
    """Führt den API-Kanarienvogel aus.

    Exit-Codes:
    0: Alles in Ordnung (Schema vollständig)
    1: Schema-Probleme erkannt (Felder fehlen, Health-Alarm ausgelöst)
    3: Netzwerk- oder SoundCloud-Fehler (kein Health-Eintrag)
    """
    install_redacting_logging(logging.INFO)

    parser = argparse.ArgumentParser(description="API-Kanarienvogel für SoundCloud-Antworten")
    parser.add_argument("--config", default="config.yaml", help="Pfad zur Konfigurationsdatei")
    parser.add_argument("--no-telegram", action="store_true", help="Keine Telegram-Nachrichten senden")
    args = parser.parse_args(argv)

    cfg = Config.load(args.config)
    canary_cfg = cfg.raw.get("canary") or {}
    tags = (cfg.raw.get("search") or {}).get("tags") or []
    tag = canary_cfg.get("tag") or (tags[0] if tags else "")
    track_url = canary_cfg.get("track_url") or None
    db_path = (cfg.raw.get("state") or {}).get("db_path") or "data/seen.sqlite"

    try:
        sc = SoundCloudClient()
        problems = run_canary(sc, tag=tag, track_url=track_url)
    except (SoundCloudError, requests.RequestException) as exc:
        log.warning("Kanarienvogel konnte SoundCloud nicht abfragen: %s", exc)
        return 3

    msg = record_canary(db_path, problems)
    if msg and not args.no_telegram:
        try:
            send_telegram(cfg, [msg])
        except Exception as exc:
            log.warning("Telegram-Benachrichtigung fehlgeschlagen: %s", exc)

    return 1 if problems else 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
