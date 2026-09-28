"""Einstiegspunkt.

  python -m sc_digger.main                                   # discover (Standard)
  python -m sc_digger.main playlist <url> [--likes]          # Playlist prüfen
  python -m sc_digger.main similar <track-url> [--filter]    # Algorithmus-Empfehlungen zu einem Track
  python -m sc_digger.main check <url>                       # wie der Telegram-Bot: Playlist oder Station

Alle Modi: --dry-run (nichts laden/senden), -v, --config, --no-telegram
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .analysis import analyze_track, resolve_bpm
from .collection import Collection
from .health import Health
from .models import Config, DownloadKind, Track
from .organize import organize, write_tags
from .redact import install_redacting_logging
from .output import (State, build_digest, build_export_txt, download_native, finalize_quality,
                     send_telegram, send_telegram_document)
from .pipeline import (classify_download, dedupe, estimate_bpm, filter_bpm, filter_sets,
                       genre_relevant, mark_sets, score_tracks)
from .soundcloud import SoundCloudClient, SoundCloudError

log = logging.getLogger("sc_digger")


# ------------------------------------------------------------------ gemeinsame Nachbearbeitung
def process(tracks: list[Track], cfg: Config, *, dry_run: bool,
            skip_duplicates: bool = True) -> tuple[list[Track], list[Track]]:
    """Duplikat-Check -> Klassifizierung -> native Downloads + Qualität + Analyse + Tagging + Organize.

    Gibt (neue Tracks, Duplikate) zurück.
    """
    mark_sets(tracks, cfg)
    coll = Collection(cfg["download"]["collection_dir"])
    coll.mark_duplicates(tracks)
    dupes = [t for t in tracks if t.duplicate_of]
    fresh = [t for t in tracks if not t.duplicate_of] if skip_duplicates else tracks

    for t in fresh:
        classify_download(t)

    if dry_run:
        return fresh, dupes

    inbox = Path(cfg["download"]["inbox_dir"])
    org = cfg.raw.get("organize", {})
    token = cfg.soundcloud_auth_token
    if not token and any(t.download_kind == DownloadKind.NATIVE for t in fresh):
        log.info("SOUNDCLOUD_AUTH_TOKEN fehlt: native Downloads werden nur verlinkt, nicht geladen")
    for t in fresh:
        if t.download_kind != DownloadKind.NATIVE:
            continue
        path = download_native(t, inbox, token)
        if not path:
            continue
        # Fakes -> _rejected/, Brickwall -> _rejected/clipped/; nur Echtes läuft weiter
        path = finalize_quality(t, path, inbox, cfg)
        if not path:
            continue

        # Audio-Analyse: BPM + Key aus dem Audiomaterial erkennen
        if org.get("detect_bpm", True) or org.get("detect_key", True):
            try:
                analysis = analyze_track(path)
                if org.get("detect_bpm", True):
                    # Text-BPM als Anker: in playlist/similar lief filter_bpm nicht
                    text_bpm = t.bpm or estimate_bpm(t)
                    s = cfg["search"]
                    t.bpm, reason = resolve_bpm(
                        analysis["bpm"], text_bpm,
                        window=(s["bpm_min"], s["bpm_max"]),
                        plausible=(org.get("bpm_plausible_min", 120), org.get("bpm_plausible_max", 200)),
                    )
                    log.debug("BPM %s für %s: %s", t.bpm, t.title, reason)
                    audio_bpm = analysis["bpm"]
                    if audio_bpm and t.bpm and abs(t.bpm - audio_bpm) > 1.0:
                        t.notes.append(f"BPM korrigiert: {reason}")
                if org.get("detect_key", True):
                    t.key_camelot = analysis["key_camelot"]
                    t.key_name = analysis["key_name"]
            except Exception as e:
                t.notes.append(f"Audio-Analyse fehlgeschlagen: {e}")

        # ID3-Tags schreiben
        if org.get("write_tags", True):
            comment = f"Score: {t.percentile:.0f}p" if t.percentile else ""
            if t.key_camelot:
                comment += f" | Key: {t.key_camelot}"
            try:
                write_tags(
                    path, artist=t.artist, title=t.title, bpm=t.bpm, key_name=t.key_name,
                    genre=t.genre or org.get("default_genre", "Schranz"),
                    comment=comment.strip(" |"), url=t.url,
                    loudness=t.quality_report,
                )
            except Exception as e:
                t.notes.append(f"Tagging fehlgeschlagen: {e}")

        # Auto-Organize: nach BPM/Key-Ordner verschieben
        if org.get("enabled", True):
            try:
                organize(path, inbox, bpm=t.bpm, key_camelot=t.key_camelot,
                         bucket_size=org.get("bpm_bucket_size", 5))
            except Exception as e:
                t.notes.append(f"Organize fehlgeschlagen: {e}")

    return fresh, dupes


def deliver(header: str, fresh: list[Track], dupes: list[Track], cfg: Config,
            *, dry_run: bool, no_telegram: bool, show_all: bool = False,
            export_name: str = "sc-digger", chat_id: str | None = None) -> None:
    """Digest als Chat-Nachrichten + Export-Datei (Anhang) für das Download-Tool."""
    max_items = None if show_all else cfg["telegram"]["max_items_per_digest"]
    messages = build_digest(fresh, max_items, header=header)
    if dupes:
        messages[-1] += f"\n<i>{len(dupes)} bereits in deiner Sammlung (übersprungen)</i>"
    if dry_run or no_telegram:
        for m in messages:
            print(re.sub(r"<[^>]+>", "", m))
        if fresh:
            print(f"(Export-Datei mit {len(fresh)} Tracks würde als Anhang gesendet)")
        return
    send_telegram(cfg, messages, chat_id=chat_id)
    if fresh:
        ts = datetime.now(ZoneInfo("Europe/Berlin"))
        slug = re.sub(r"[^a-z0-9]+", "-", export_name.lower()).strip("-")[:40] or "export"
        send_telegram_document(cfg, f"sc-digger-{slug}-{ts:%Y-%m-%d_%H%M}.txt",
                               build_export_txt(fresh, folder_name=export_name), chat_id=chat_id)


# ------------------------------------------------------------------ Modus: discover
def run_discover(cfg: Config, dry_run: bool, no_telegram: bool) -> None:
    """Discovery mit Health-Protokoll. Dry-Runs werden nicht protokolliert."""
    if dry_run:
        _discover(cfg, dry_run, no_telegram)
        return

    threshold = cfg.raw.get("health", {}).get("alert_after_bad_runs", 2)
    with Health(cfg["state"]["db_path"]) as health:
        try:
            found = _discover(cfg, dry_run, no_telegram)
        except Exception as e:
            health.record("discover", ok=False, found=0, error=f"{type(e).__name__}: {e}")
            _notify_health(cfg, health.evaluate("discover", threshold), no_telegram)
            raise
        health.record("discover", ok=True, found=found)
        _notify_health(cfg, health.evaluate("discover", threshold), no_telegram)


def _notify_health(cfg: Config, message: str | None, no_telegram: bool) -> None:
    if not message:
        return
    log.warning("Health: %s", message.replace("\n", " | "))
    if no_telegram:
        return
    try:
        send_telegram(cfg, [message])
    except Exception as e:  # Alarm darf den ursprünglichen Fehler nicht überdecken
        log.error("Health-Alarm konnte nicht gesendet werden: %s", e)


def _discover(cfg: Config, dry_run: bool, no_telegram: bool) -> int:
    """Führt die Discovery aus und gibt die Zahl der Rohtreffer (vor Filtern) zurück."""
    sc, s = SoundCloudClient(), cfg["search"]
    tracks: list[Track] = []
    for tag in s["tags"]:
        tracks += sc.search_tag(tag, s["max_age_days"], s["limit_per_tag"])
    for profile in s.get("followed_users", []):
        try:
            tracks += sc.user_uploads(profile, s["max_age_days"])
        except Exception as e:
            log.warning("Profil %s übersprungen: %s", profile, e)
    # Reposts/Likes von Referenz-Accounts: bestes Signal, bekommt Score-Bonus
    reference_ids: set[int] = set()
    for profile in s.get("reference_accounts", []):
        try:
            ref = sc.reference_activity(profile, s["max_age_days"], s.get("reference_limit", 50))
        except Exception as e:
            log.warning("Referenz-Account %s übersprungen: %s", profile, e)
            continue
        ref = [t for t in ref if genre_relevant(t, s["tags"])]
        reference_ids.update(t.id for t in ref)
        tracks += ref
    tracks = dedupe(tracks)
    for t in tracks:
        t.reference_hit = t.id in reference_ids
    raw_found = len(tracks)
    log.info("Discovery: %d einzigartige Tracks (davon %d von Referenz-Accounts)",
             raw_found, len(reference_ids))

    tracks = score_tracks(filter_bpm(filter_sets(tracks, cfg), cfg), cfg)
    log.info("Nach Filter/Scoring: %d Tracks", len(tracks))
    with State(cfg["state"]["db_path"]) as state:
        tracks = [t for t in tracks if not state.is_seen(t.id)]
        fresh, dupes = process(tracks, cfg, dry_run=dry_run)
        log.info("Neu: %d (Duplikate in Sammlung: %d)", len(fresh), len(dupes))

        deliver(f"sc-digger – {len(fresh)} neue Treffer", fresh, dupes, cfg,
                dry_run=dry_run, no_telegram=no_telegram, export_name="Täglicher Digest")
        if not dry_run:
            # Erst nach erfolgreichem Versand markieren: bei Fehler in send_telegram
            # werden Tracks beim nächsten Lauf erneut gemeldet statt verloren zu gehen.
            for t in fresh:
                state.mark_one(t)
    return raw_found


# ------------------------------------------------------------------ Modus: playlist
def run_playlist(cfg: Config, url: str, likes: bool, dry_run: bool, no_telegram: bool) -> None:
    """Playlist prüfen: KEIN Genre-/Score-Filter, nur Sammlungsabgleich + Download-Weg.

    Die Playlist wurde bewusst gewählt, deshalb wird nichts aussortiert.
    Kein State-Check: eine Playlist darf man beliebig oft prüfen.
    """
    sc = SoundCloudClient()
    title, tracks = sc.likes_of_user(url) if likes else sc.playlist_tracks(url)
    log.info("%s: %d Tracks", title, len(tracks))
    fresh, dupes = process(dedupe(tracks), cfg, dry_run=dry_run)
    deliver(f"Playlist „{title}“: {len(fresh)} fehlen, {len(dupes)} vorhanden",
            fresh, dupes, cfg, dry_run=dry_run, no_telegram=no_telegram, show_all=True,
            export_name=f"Playlist: {title}")


# ------------------------------------------------------------------ Modus: check (Link, auch vom Bot)
def run_link(cfg: Config, url: str, *, dry_run: bool = False, no_telegram: bool = False,
             chat_id: str | None = None, sc: SoundCloudClient | None = None) -> tuple[str, int, int]:
    """On-Demand-Check eines beliebigen Links: Playlist -> alle Tracks, Track -> Station.

    Kein Perzentil-Filter (selbst gewählte Quelle: volle Liste), aber Score-Sortierung.
    Tracks werden als gesehen markiert, damit sie nicht nochmal im täglichen Digest landen.
    Gibt (Quellenbeschreibung, neu, vorhanden) zurück.
    """
    sc = sc or SoundCloudClient()
    kind = sc.resolve_kind(url)
    if kind == "playlist":
        label, tracks = sc.playlist_tracks(url, limit=200)
        source = f"Playlist: {label}"
    elif kind == "track":
        label, tracks = sc.track_station(url, limit=50)
        source = f"Station zu: {label}"
    else:
        raise SoundCloudError("Das ist weder ein Track- noch ein Playlist-Link.")

    tracks = dedupe(tracks)
    for t in tracks:
        t.bpm = estimate_bpm(t)
    tracks = score_tracks(tracks, cfg, apply_filter=False)
    fresh, dupes = process(tracks, cfg, dry_run=dry_run)
    deliver(f"{source}\n{len(fresh)} Tracks ({len(dupes)} schon in deiner Sammlung)",
            fresh, dupes, cfg, dry_run=dry_run, no_telegram=no_telegram, show_all=True,
            export_name=source, chat_id=chat_id)
    if not dry_run:
        with State(cfg["state"]["db_path"]) as state:
            state.mark(tracks)
    return source, len(fresh), len(dupes)


# ------------------------------------------------------------------ Modus: similar
def run_similar(cfg: Config, url: str, use_station: bool, apply_filter: bool,
                limit: int, dry_run: bool, no_telegram: bool) -> None:
    """Empfehlungen des SoundCloud-Algorithmus zu einem Track.

    Standard: ungefiltert (du willst sehen, was der Algorithmus vorschlägt).
    Mit --filter laufen die Treffer zusätzlich durch BPM-Fenster + Scoring.
    """
    sc = SoundCloudClient()
    seed = sc.resolve_track(url)
    log.info("Seed: %s – %s", seed.artist, seed.title)
    tracks = sc.station(seed.id, limit) if use_station else sc.related(seed.id, limit)
    tracks = [t for t in dedupe(tracks) if t.id != seed.id]

    if apply_filter:
        tracks = score_tracks(filter_bpm(tracks, cfg), cfg)
    else:
        tracks.sort(key=lambda t: t.like_ratio, reverse=True)

    fresh, dupes = process(tracks, cfg, dry_run=dry_run)
    kind = "Track-Radio" if use_station else "Related"
    deliver(f"{kind} zu „{seed.artist} – {seed.title}“: {len(fresh)} neu",
            fresh, dupes, cfg, dry_run=dry_run, no_telegram=no_telegram,
            export_name=f"{kind}: {seed.artist} - {seed.title}")


# ------------------------------------------------------------------ CLI
def cli() -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="config.yaml")
    common.add_argument("--dry-run", action="store_true",
                        help="Nichts laden, nichts senden, State nicht schreiben")
    common.add_argument("--no-telegram", action="store_true",
                        help="Ergebnis in der Konsole ausgeben statt an Telegram")
    common.add_argument("-v", "--verbose", action="store_true")

    ap = argparse.ArgumentParser(description="sc-digger", parents=[common])
    sub = ap.add_subparsers(dest="mode")
    sub.add_parser("discover", parents=[common], help="Tag-/Künstler-Suche (Standard)")

    p = sub.add_parser("playlist", parents=[common], help="Playlist gegen Sammlung prüfen")
    p.add_argument("url")
    p.add_argument("--likes", action="store_true", help="URL ist ein Profil: dessen Likes prüfen")

    s = sub.add_parser("similar", parents=[common], help="Algorithmus-Empfehlungen zu einem Track")
    s.add_argument("url")
    s.add_argument("--radio", action="store_true", help="Track-Radio statt Related Tracks")
    s.add_argument("--filter", action="store_true", help="BPM-Fenster + Scoring anwenden")
    s.add_argument("--limit", type=int, default=50)

    c = sub.add_parser("check", parents=[common],
                       help="Beliebigen Link prüfen wie der Telegram-Bot (Playlist oder Track-Station)")
    c.add_argument("url")

    a = ap.parse_args()
    install_redacting_logging(logging.DEBUG if a.verbose else logging.INFO)
    cfg = Config.load(a.config)
    try:
        if a.mode == "playlist":
            run_playlist(cfg, a.url, a.likes, a.dry_run, a.no_telegram)
        elif a.mode == "similar":
            run_similar(cfg, a.url, a.radio, a.filter, a.limit, a.dry_run, a.no_telegram)
        elif a.mode == "check":
            run_link(cfg, a.url, dry_run=a.dry_run, no_telegram=a.no_telegram)
        else:
            run_discover(cfg, a.dry_run, a.no_telegram)
    except Exception:
        # Über logging statt Python-Standard-Traceback: nur so greift die Token-Bereinigung
        log.exception("Lauf fehlgeschlagen")
        sys.exit(1)


if __name__ == "__main__":
    cli()
