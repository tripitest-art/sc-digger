"""Einstiegspunkt.

  python -m sc_digger.main                                   # discover (Standard)
  python -m sc_digger.main playlist <url> [--likes]          # Playlist prüfen
  python -m sc_digger.main similar <track-url> [--filter]    # Algorithmus-Empfehlungen zu einem Track

Alle Modi: --dry-run (nichts laden/senden), -v, --config, --no-telegram
"""
from __future__ import annotations

import argparse
import logging
import shutil
from pathlib import Path

from .analysis import analyze_track
from .collection import Collection
from .models import Config, DownloadKind, Track
from .organize import organize, write_tags
from .output import State, build_digest, download_native, send_telegram
from .pipeline import classify_download, dedupe, filter_bpm, score_tracks
from .quality import check_file
from .soundcloud import SoundCloudClient

log = logging.getLogger("sc_digger")


# ------------------------------------------------------------------ gemeinsame Nachbearbeitung
def process(tracks: list[Track], cfg: Config, *, dry_run: bool,
            skip_duplicates: bool = True) -> tuple[list[Track], list[Track]]:
    """Duplikat-Check -> Klassifizierung -> native Downloads + Qualität + Analyse + Tagging + Organize.

    Gibt (neue Tracks, Duplikate) zurück.
    """
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
    for t in fresh:
        if t.download_kind != DownloadKind.NATIVE:
            continue
        path = download_native(t, inbox)
        if not path:
            t.notes.append("Download fehlgeschlagen")
            continue
        try:
            t.quality_report = check_file(path, cfg)
        except Exception as e:
            t.notes.append(f"Qualitätsprüfung fehlgeschlagen: {e}")
            continue
        if not t.quality_report["ok"]:
            rejected = inbox / "_rejected"
            rejected.mkdir(exist_ok=True)
            shutil.move(str(path), str(rejected / path.name))
            continue

        # Audio-Analyse: BPM + Key aus dem Audiomaterial erkennen
        if org.get("detect_bpm", True) or org.get("detect_key", True):
            try:
                analysis = analyze_track(path)
                if org.get("detect_bpm", True):
                    if analysis["bpm"] and not t.bpm:
                        t.bpm = analysis["bpm"]
                    elif analysis["bpm"] and t.bpm:
                        log.debug("BPM Text=%.0f, Audio=%.1f für %s", t.bpm, analysis["bpm"], t.title)
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
            *, dry_run: bool, no_telegram: bool, show_all: bool = False) -> None:
    max_items = None if show_all else cfg["telegram"]["max_items_per_digest"]
    messages = build_digest(fresh, max_items, header=header)
    if dupes:
        messages[-1] += f"\n<i>{len(dupes)} bereits in deiner Sammlung (übersprungen)</i>"
    if dry_run or no_telegram:
        import re
        for m in messages:
            print(re.sub(r"<[^>]+>", "", m))
    else:
        send_telegram(cfg, messages)


# ------------------------------------------------------------------ Modus: discover
def run_discover(cfg: Config, dry_run: bool, no_telegram: bool) -> None:
    sc, s = SoundCloudClient(), cfg["search"]
    tracks: list[Track] = []
    for tag in s["tags"]:
        tracks += sc.search_tag(tag, s["max_age_days"], s["limit_per_tag"])
    for profile in s.get("followed_users", []):
        try:
            tracks += sc.user_uploads(profile, s["max_age_days"])
        except Exception as e:
            log.warning("Profil %s übersprungen: %s", profile, e)
    tracks = dedupe(tracks)
    log.info("Discovery: %d einzigartige Tracks", len(tracks))

    tracks = score_tracks(filter_bpm(tracks, cfg), cfg)
    with State(cfg["state"]["db_path"]) as state:
        tracks = [t for t in tracks if not state.is_seen(t.id)]
        fresh, dupes = process(tracks, cfg, dry_run=dry_run)

        deliver(f"sc-digger – {len(fresh)} neue Treffer", fresh, dupes, cfg,
                dry_run=dry_run, no_telegram=no_telegram)
        if not dry_run:
            # Erst nach erfolgreichem Versand markieren: bei Fehler in send_telegram
            # werden Tracks beim nächsten Lauf erneut gemeldet statt verloren zu gehen.
            for t in fresh:
                state.mark_one(t)


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
            fresh, dupes, cfg, dry_run=dry_run, no_telegram=no_telegram, show_all=True)


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
            fresh, dupes, cfg, dry_run=dry_run, no_telegram=no_telegram)


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

    a = ap.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = Config.load(a.config)
    if a.mode == "playlist":
        run_playlist(cfg, a.url, a.likes, a.dry_run, a.no_telegram)
    elif a.mode == "similar":
        run_similar(cfg, a.url, a.radio, a.filter, a.limit, a.dry_run, a.no_telegram)
    else:
        run_discover(cfg, a.dry_run, a.no_telegram)


if __name__ == "__main__":
    cli()
