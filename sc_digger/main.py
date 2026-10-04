"""Einstiegspunkt.

  python -m sc_digger.main                                   # discover (Standard)
  python -m sc_digger.main playlist <url> [--likes]          # Playlist prüfen
  python -m sc_digger.main similar <track-url> [--filter]    # Algorithmus-Empfehlungen zu einem Track
  python -m sc_digger.main check <url>                       # wie der Telegram-Bot: Playlist oder Station
  python -m sc_digger.main intake [--dry-run]                # Manuell abgelegte Tracks verarbeiten
  python -m sc_digger.main audit [--path ...] [--report ...] # Library-Audit (read-only)
  python -m sc_digger.main scout [--dry-run]                 # Bandcamp-Feeds + Beatport-Charts in store_items

Alle Modi: --dry-run (nichts laden/senden), -v, --config, --no-telegram
"""
from __future__ import annotations

import argparse
import html
import logging
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .analysis import analyze_track, resolve_bpm
from .audit import run_audit
from .cloud import download_cloud
from .collection import Collection
from .db import TrackDB
from .health import Health
from .intake import AUDIO_EXTS, find_ready_files, track_from_file
from .models import Config, DownloadKind, Track
from .organize import organize, write_tags
from .redact import install_redacting_logging, redact
from .output import (DigestMessage, State, TelegramError, build_digest, build_digest_messages, build_export_txt, download_native,
                     finalize_quality, send_digest, send_scout_digest, send_scout_error, send_telegram, send_telegram_document)
from .pipeline import (classify_download, dedupe, estimate_bpm, filter_bpm, filter_sets,
                       genre_relevant, mark_sets, score_tracks)
from .rekordbox import write_rekordbox_xml
from .retry import RetryQueue
from .scout.bandcamp import fetch_bandcamp_feeds
from .scout.beatport import fetch_beatport_charts
from .soundcloud import ClientIdError, RateLimitError, SoundCloudClient, SoundCloudError
from .stats import calculate_stats, format_stats, render_stats_chart, send_weekly_digest

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
    retry_max = (cfg.raw.get("retry") or {}).get("max_attempts", 3)
    if not token and any(t.download_kind == DownloadKind.NATIVE for t in fresh):
        log.info("SOUNDCLOUD_AUTH_TOKEN fehlt: native Downloads werden nur verlinkt, nicht geladen")
    retry_q: RetryQueue | None = None
    try:
        for t in fresh:
            if t.download_kind == DownloadKind.NATIVE:
                path = download_native(t, inbox, token)
                if token:
                    if retry_q is None:
                        retry_q = RetryQueue(cfg["state"]["db_path"], max_attempts=retry_max)
                    if path:
                        retry_q.record_success(t.id)
                    else:
                        reason = t.notes[-1] if t.notes else "Original-Download fehlgeschlagen"
                        retry_q.record_failure(t, reason)
            elif t.download_kind == DownloadKind.CLOUD and not cfg["download"].get("auto_download_native_only", True):
                path = download_cloud(t, inbox, max_mb=cfg["download"].get("cloud_max_mb", 500))
            else:
                continue
            if not path:
                continue
            finish_file(t, path, inbox, cfg)
    finally:
        if retry_q:
            retry_q.close()

    return fresh, dupes


def finish_file(t: Track, path: Path, inbox: Path, cfg: Config) -> Path | None:
    """Der bisherige Teil von process() NACH dem Download, unverändert herausgelöst:
    finalize_quality -> analyze_track/resolve_bpm -> write_tags -> organize.
    Rückgabe: neuer Pfad der Datei oder None, wenn finalize_quality sie aussortiert hat.
    process() ruft finish_file für jeden geladenen Track auf; sein Verhalten ändert sich nicht.
    """
    org = cfg.raw.get("organize", {})

    # Fakes -> _rejected/, Brickwall -> _rejected/clipped/; nur Echtes läuft weiter
    path = finalize_quality(t, path, inbox, cfg)
    if not path:
        return None

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
            path = organize(path, inbox, bpm=t.bpm, key_camelot=t.key_camelot,
                            bucket_size=org.get("bpm_bucket_size", 5))
        except Exception as e:
            t.notes.append(f"Organize fehlgeschlagen: {e}")

    return path


# ------------------------------------------------------------------ Eingangsordner (manuell geladene Tracks)
def run_intake(cfg: Config, *, dry_run: bool = False) -> list[str]:
    """Verarbeitet find_ready_files(download.intake_dir, download.intake_min_age_s).
    intake_dir fehlt/leer -> [] (abgeschaltet). Legt den Ordner an (nicht im Dry-Run), damit
    Stephan ihn auf dem Laptop sieht. Dry-Run: nichts verschieben, nichts schreiben.
    Pro Datei:
      - Endung nicht in AUDIO_EXTS -> nach <intake>/_unbekannt/ verschieben
      - sonst t = track_from_file(p); res = finish_file(t, p, inbox, cfg)
          res gesetzt          -> "✅ <Artist> – <Titel> · <BPM> BPM · <Camelot>"
          Datei liegt noch da  -> (Prüfung selbst scheiterte) nach <intake>/_fehler/ verschieben
          t.duplicate_of       -> Zeile "♻️ <Dateiname>: schon in der Sammlung"
          sonst                -> Zeile "❌ <Dateiname>: <quality_report["reason"]>"
    Damit liegt nach einem Aufruf keine verarbeitete Datei mehr im Eingang, und jede Datei
    wird genau einmal gemeldet.
    Rückgabe: Digest-Zeilen (Klartext), leer wenn keine Datei bereit war; erste Zeile
    "📥 Eingang: <n> Dateien verarbeitet", danach eine Zeile je Datei.
    """
    import shutil

    intake_dir_str = (cfg.raw.get("download") or {}).get("intake_dir", "")
    if not intake_dir_str:
        return []

    intake_dir = Path(intake_dir_str)
    if not dry_run:
        try:
            intake_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.debug("Eingangsordner konnte nicht angelegt werden: %s", e)
            return []
    elif not intake_dir.exists():
        return []

    min_age = float((cfg.raw.get("download") or {}).get("intake_min_age_s", 120))
    files = find_ready_files(intake_dir, min_age)
    if not files:
        return []

    if dry_run:
        lines: list[str] = []
        for p in files:
            ext = p.suffix.lower()
            if ext not in AUDIO_EXTS:
                lines.append(f"⚠️ {p.name}: kein Audio-Format")
            else:
                t = track_from_file(p)
                display_name = f"{t.artist} – {t.title}" if t.artist else t.title
                lines.append(f"📁 {display_name} ({p.name})")
        header = f"📥 Eingang: {len(files)} Datei{'en' if len(files) != 1 else ''} bereit (Dry-Run)"
        return [header] + lines

    inbox = Path(cfg["download"]["inbox_dir"])
    lines = []

    for p in files:
        ext = p.suffix.lower()
        if ext not in AUDIO_EXTS:
            dest = intake_dir / "_unbekannt"
            dest.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest / p.name))
            lines.append(f"⚠️ {p.name}: kein Audio-Format (nach _unbekannt verschoben)")
            continue

        t = track_from_file(p)
        try:
            res = finish_file(t, p, inbox, cfg)
        except Exception as e:
            log.warning("finish_file fehlgeschlagen für %s: %s", p.name, e)
            t.notes.append(f"Verarbeitung fehlgeschlagen: {e}")
            res = None

        if res is not None:
            bpm_str = f"{t.bpm:.0f}" if t.bpm else "?"
            key_str = t.key_camelot or "?"
            display_name = f"{t.artist} – {t.title}" if t.artist else t.title
            lines.append(f"✅ {display_name} · {bpm_str} BPM · {key_str}")
        elif p.exists():
            err_dir = intake_dir / "_fehler"
            err_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(err_dir / p.name))
            reason = t.notes[-1] if t.notes else "Prüfung fehlgeschlagen"
            lines.append(f"❌ {p.name}: {reason}")
        elif t.duplicate_of:
            lines.append(f"♻️ {p.name}: schon in der Sammlung")
        else:
            if t.quality_report and t.quality_report.get("clipped"):
                reason = "Brickwall-Master (Clipping)"
            elif t.quality_report and t.quality_report.get("reason"):
                reason = t.quality_report["reason"]
            else:
                reason = "abgelehnt"
            lines.append(f"❌ {p.name}: {reason}")

    header = f"📥 Eingang: {len(files)} Datei{'en' if len(files) != 1 else ''} verarbeitet"
    return [header] + lines


def deliver(header: str, fresh: list[Track], dupes: list[Track], cfg: Config,
            *, dry_run: bool, no_telegram: bool, show_all: bool = False,
            export_name: str = "sc-digger", chat_id: str | None = None,
            footer: str | None = None) -> None:
    """Digest als Chat-Nachrichten + Export-Datei (Anhang) für das Download-Tool."""
    max_items = None if show_all else cfg["telegram"]["max_items_per_digest"]
    buttons = bool(cfg["telegram"].get("feedback_buttons", True))
    messages = build_digest_messages(fresh, max_items, header=header, numbered=buttons)
    if dupes and messages:
        messages[-1].text += f"\n<i>{len(dupes)} bereits in deiner Sammlung (übersprungen)</i>"
    if footer:
        esc_footer = html.escape(footer)
        if messages and len(messages[-1].text) + len(esc_footer) + 2 <= 3900:
            sep = "\n" if messages[-1].text.endswith("\n") else "\n\n"
            messages[-1].text = f"{messages[-1].text}{sep}{esc_footer}"
        else:
            while len(esc_footer) > 3900:
                messages.append(DigestMessage(text=esc_footer[:3900], items=[]))
                esc_footer = esc_footer[3900:]
            if esc_footer:
                messages.append(DigestMessage(text=esc_footer, items=[]))
    if dry_run or no_telegram:
        for m in messages:
            print(re.sub(r"<[^>]+>", "", m.text))
        if fresh:
            print(f"(Export-Datei mit {len(fresh)} Tracks würde als Anhang gesendet)")
        return
    send_digest(cfg, messages, chat_id=chat_id, buttons=buttons)
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


@dataclass
class Discovery:
    tracks: list[Track]                 # alle Treffer aller erfolgreichen Quellen (noch nicht dedupliziert)
    reference_ids: set[int]             # IDs aus Referenz-Accounts (nach genre_relevant, wie bisher)
    total_sources: int                  # Anzahl Tags + followed_users + reference_accounts
    succeeded: int                      # Quellen ohne Exception
    failed: list[str]                   # je gescheiterter Quelle: "<Quelle>: <Fehlertyp>: <Meldung>",
                                        #   durch redact(), höchstens 200 Zeichen; <Quelle> enthält den Tag
                                        #   bzw. die Profil-URL
    aborted: str | None                 # gesetzt, wenn nach ClientIdError/RateLimitError abgebrochen wurde
    first_error: Exception | None       # erste aufgetretene Exception
    exploration_used: list[str] = field(default_factory=list)  # Exploration-Tags, deren Suche gelang


def select_exploration_tags(
    exploration_tags: list[str],
    existing_tags: list[str],
    probability: float = 0.0,
    rng: random.Random | None = None,
) -> list[str]:
    """Wählt höchstens EINEN Explorations-Tag.
    - probability <= 0 -> [] (rng wird dann nicht benutzt)
    - Kandidaten = exploration_tags ohne die, die schon in existing_tags stehen; leer -> []
    - rng is None -> random.Random()
    - rng.random() < probability -> [rng.choice(kandidaten)], sonst []"""
    if probability <= 0:
        return []
    candidates = [t for t in exploration_tags if t not in existing_tags]
    if not candidates:
        return []
    if rng is None:
        rng = random.Random()
    if rng.random() < probability:
        return [rng.choice(candidates)]
    return []


def collect_sources(sc: SoundCloudClient, s: dict, rng: random.Random | None = None) -> Discovery:
    """Fragt die Quellen in dieser Reihenfolge ab: alle Tags, dann die gewählten
    Explorations-Tags, dann followed_users, dann reference_accounts. Jede Quelle einzeln in
    try/except Exception; ein Fehler wird in `failed` eingetragen und die nächste Quelle
    abgefragt. Nach ClientIdError oder RateLimitError wird KEINE weitere Quelle mehr abgefragt
    (aborted gesetzt). Wirft nie."""
    tags = list(s.get("tags", []))
    followed = list(s.get("followed_users", []))
    reference = list(s.get("reference_accounts", []))
    exploration = select_exploration_tags(
        s.get("exploration_tags", []),
        tags,
        s.get("exploration_probability", 0.0),
        rng,
    )
    total_sources = len(tags) + len(exploration) + len(followed) + len(reference)

    tracks: list[Track] = []
    reference_ids: set[int] = set()
    exploration_used: list[str] = []
    succeeded = 0
    failed: list[str] = []
    aborted: str | None = None
    first_error: Exception | None = None

    # Tags
    for tag in tags:
        try:
            tracks.extend(sc.search_tag(tag, s["max_age_days"], s["limit_per_tag"]))
            succeeded += 1
        except Exception as e:
            if first_error is None:
                first_error = e
            failed.append(redact(f"{tag}: {type(e).__name__}: {e}")[:200])
            log.warning("Tag %s fehlgeschlagen: %s", tag, e)
            if isinstance(e, (ClientIdError, RateLimitError)):
                aborted = type(e).__name__
                break

    # Explorations-Tags (wie ein Tag, nach den regulären Tags und vor followed_users)
    if not aborted:
        for tag in exploration:
            try:
                found = sc.search_tag(tag, s["max_age_days"], s["limit_per_tag"])
                for t in found:
                    t.exploration_tag = tag
                tracks.extend(found)
                exploration_used.append(tag)
                succeeded += 1
            except Exception as e:
                if first_error is None:
                    first_error = e
                failed.append(redact(f"{tag}: {type(e).__name__}: {e}")[:200])
                log.warning("Explorations-Tag %s fehlgeschlagen: %s", tag, e)
                if isinstance(e, (ClientIdError, RateLimitError)):
                    aborted = type(e).__name__
                    break

    # Followed users
    if not aborted:
        for profile in followed:
            try:
                tracks.extend(sc.user_uploads(profile, s["max_age_days"]))
                succeeded += 1
            except Exception as e:
                if first_error is None:
                    first_error = e
                failed.append(redact(f"{profile}: {type(e).__name__}: {e}")[:200])
                log.warning("Profil %s fehlgeschlagen: %s", profile, e)
                if isinstance(e, (ClientIdError, RateLimitError)):
                    aborted = type(e).__name__
                    break

    # Reference accounts
    if not aborted:
        for profile in reference:
            try:
                ref = sc.reference_activity(profile, s["max_age_days"], s.get("reference_limit", 50))
                ref = [t for t in ref if genre_relevant(t, tags)]
                reference_ids.update(t.id for t in ref)
                tracks.extend(ref)
                succeeded += 1
            except Exception as e:
                if first_error is None:
                    first_error = e
                failed.append(redact(f"{profile}: {type(e).__name__}: {e}")[:200])
                log.warning("Referenz-Account %s fehlgeschlagen: %s", profile, e)
                if isinstance(e, (ClientIdError, RateLimitError)):
                    aborted = type(e).__name__
                    break

    return Discovery(
        tracks=tracks,
        reference_ids=reference_ids,
        total_sources=total_sources,
        succeeded=succeeded,
        failed=failed,
        aborted=aborted,
        first_error=first_error,
        exploration_used=exploration_used,
    )


def source_footer(d: Discovery) -> str | None:
    """None, wenn nichts fehlschlug. Sonst Klartext (kein HTML):
    Zeile 1: "⚠️ <len(failed)> von <total_sources> Quellen fehlgeschlagen: <failed[0]>"
    Zeile 2 (nur wenn aborted): "Restliche Quellen übersprungen (<aborted>)"."""
    if not d.failed:
        return None
    lines = [f"⚠️ {len(d.failed)} von {d.total_sources} Quellen fehlgeschlagen: {d.failed[0]}"]
    if d.aborted:
        lines.append(f"Restliche Quellen übersprungen ({d.aborted})")
    return "\n".join(lines)


def retry_downloads(sc: SoundCloudClient, cfg: Config) -> list[str]:
    """Arbeitet RetryQueue.due() ab. Pro Eintrag: t = sc.resolve_track(item.url), dann
    process([t], cfg, dry_run=False). Damit landen Erfolg und Fehlschlag über process() in der Queue.
    - resolve_track wirft -> record_failure mit dem Fehlertext
    - Track inzwischen in der Sammlung (t.duplicate_of) oder nicht mehr NATIVE -> record_success
      (nichts mehr zu tun, keine Digest-Zeile)
    Rückgabe: Digest-Zeilen (Klartext), leer wenn nichts zu melden:
      "🔄 <N> nachgeholt: <Artist> – <Title>, ..."               (Eintrag danach weg)
      "❌ <N> endgültig fehlgeschlagen (<max> Versuche): <Artist> – <Title>, ..."
                                                    (Status wechselte in diesem Aufruf auf "failed")
    Ein endgültig gescheiterter Track wird genau einmal gemeldet (due() liefert ihn danach nicht mehr).
    Wirft nicht wegen einzelner Einträge."""
    retry_cfg = cfg.raw.get("retry") or {}
    max_attempts = retry_cfg.get("max_attempts", 3)
    recovered: list[str] = []
    failed_final: list[str] = []

    with RetryQueue(cfg["state"]["db_path"], max_attempts=max_attempts) as q:
        items = q.due()
        for item in items:
            try:
                try:
                    t = sc.resolve_track(item.url)
                except Exception as e:
                    new_status = q.record_failure(item, f"{type(e).__name__}: {e}")
                    if new_status == "failed":
                        failed_final.append(f"{item.artist} – {item.title}")
                    continue

                process([t], cfg, dry_run=False)
                if t.duplicate_of or t.download_kind != DownloadKind.NATIVE:
                    q.record_success(item.sc_id)
                    continue

                status = q.status(item.sc_id)
                if status is None:
                    recovered.append(f"{t.artist} – {t.title}")
                elif status == "failed":
                    failed_final.append(f"{t.artist} – {t.title}")
            except Exception as e:
                log.warning("Fehler beim Retry von %s: %s", item.url, e)
                try:
                    new_status = q.record_failure(item, f"{type(e).__name__}: {e}")
                    if new_status == "failed":
                        failed_final.append(f"{item.artist} – {item.title}")
                except Exception:
                    pass

    lines: list[str] = []
    if recovered:
        lines.append(f"🔄 {len(recovered)} nachgeholt: {', '.join(recovered)}")
    if failed_final:
        lines.append(f"❌ {len(failed_final)} endgültig fehlgeschlagen ({max_attempts} Versuche): {', '.join(failed_final)}")
    return lines


def record_exploration_stats(cfg: Config, used_tags: list[str], fresh: list[Track],
                             now: datetime | None = None) -> None:
    """Zählt je verwendetem Exploration-Tag eine Verwendung; Erfolg = mindestens ein neuer,
    ausgelieferter Track (fresh) mit diesem exploration_tag. Ohne Tags: keine DB-Zugriffe."""
    if not used_tags:
        return
    hits = {t.exploration_tag for t in fresh if t.exploration_tag}
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        for tag in used_tags:
            db.record_exploration_use(tag, tag in hits, now=now)


def is_sunday(now: datetime | None = None) -> bool:
    """True, wenn *now* (Standard: jetzt in Europe/Berlin) ein Sonntag ist."""
    if now is None:
        now = datetime.now(ZoneInfo("Europe/Berlin"))
    return now.weekday() == 6


def snapshot_tracks(cfg: Config, tracks: list[Track], *, dry_run: bool = False,
                    recorded_at: str | None = None) -> int:
    """Speichert Engagement-Snapshots für den gescorten Pool (höchstens einer je Track/Tag).

    Bewusst vor dem `is_seen`-Filter aufgerufen: sonst gäbe es pro Track nur am Tag der
    ersten Meldung einen Snapshot und nie ein Wachstum zu messen. `dry_run` schreibt
    nichts; jeder Fehler wird geloggt, aber nie weitergeworfen (Regel 7), Rückgabe dann 0.
    """
    if dry_run:
        return 0
    try:
        with TrackDB(cfg["state"]["track_db_path"]) as db:
            return db.record_track_snapshots_daily(tracks, recorded_at=recorded_at)
    except Exception:
        log.exception("Engagement-Snapshots fehlgeschlagen")
        return 0


def _discover(cfg: Config, dry_run: bool, no_telegram: bool) -> int:
    """Führt die Discovery aus und gibt die Zahl der Rohtreffer (vor Filtern) zurück."""
    sc, s = SoundCloudClient(), cfg["search"]
    retry_lines: list[str] = []
    intake_lines: list[str] = []
    if not dry_run:
        retry_lines = retry_downloads(sc, cfg)
        intake_lines = run_intake(cfg)
    d = collect_sources(sc, s)
    if d.succeeded == 0 and d.first_error:
        raise d.first_error

    tracks = dedupe(d.tracks)
    for t in tracks:
        t.reference_hit = t.id in d.reference_ids
    raw_found = len(tracks)
    log.info("Discovery: %d einzigartige Tracks (davon %d von Referenz-Accounts)",
             raw_found, len(d.reference_ids))

    tracks = score_tracks(filter_bpm(filter_sets(tracks, cfg), cfg), cfg)
    log.info("Nach Filter/Scoring: %d Tracks", len(tracks))
    # Vor dem is_seen-Filter und außerhalb des State-Blocks: so bekommt auch bereits
    # gemeldeter Pool jeden Tag genau einen Snapshot (Grundlage für den Trend-Radar).
    if not dry_run:
        snapshot_tracks(cfg, tracks)
    with State(cfg["state"]["db_path"]) as state:
        tracks = [t for t in tracks if not state.is_seen(t.id)]
        fresh, dupes = process(tracks, cfg, dry_run=dry_run)
        log.info("Neu: %d (Duplikate in Sammlung: %d)", len(fresh), len(dupes))

        footer_parts = list(retry_lines) + list(intake_lines)
        sf = source_footer(d)
        if sf:
            footer_parts.append(sf)
        footer = "\n".join(footer_parts) if footer_parts else None

        deliver(f"sc-digger – {len(fresh)} neue Treffer", fresh, dupes, cfg,
                dry_run=dry_run, no_telegram=no_telegram, export_name="Täglicher Digest",
                footer=footer)
        if not dry_run:
            try:
                record_exploration_stats(cfg, d.exploration_used, fresh)
            except Exception:
                log.exception("Exploration-Statistik fehlgeschlagen")
            # Erst nach erfolgreichem Versand markieren: bei Fehler in send_telegram
            # werden Tracks beim nächsten Lauf erneut gemeldet statt verloren zu gehen.
            for t in fresh:
                state.mark_one(t)
    if not dry_run:
        write_rekordbox_xml(cfg)
    # Sonntags-Digest hängt nur am täglichen Lauf, Standard aus. Ein Fehler hier darf
    # die Discovery nicht als gescheitert erscheinen lassen.
    if is_sunday() and cfg["digest"].get("sunday_summary", False):
        try:
            send_weekly_digest(cfg, dry_run=dry_run, no_telegram=no_telegram)
        except Exception:
            log.exception("Sonntags-Digest fehlgeschlagen")
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


# ------------------------------------------------------------------ Modus: stats
def run_stats(cfg: Config, *, days: int = 7, chart_path: str | None = None,
              send: bool = False, dry_run: bool = False, no_telegram: bool = False) -> None:
    """Wochenstatistik manuell: Text auf der Konsole, optional Diagramm und Telegram.

    Ohne --send wird nichts verschickt, nur formatiert ausgegeben. Mit --send läuft die
    Ausgabe über send_weekly_digest (bei --dry-run/--no-telegram nur Konsole). --chart
    speichert das Balkendiagramm als PNG; gibt es keine Daten oder kein matplotlib, kommt
    nur ein Hinweis statt der Datei.
    """
    if send:
        stats = send_weekly_digest(cfg, days=days, chart=bool(chart_path),
                                   dry_run=dry_run, no_telegram=no_telegram)
    else:
        stats = calculate_stats(cfg["state"]["track_db_path"], cfg["state"]["db_path"], days=days)
        print(format_stats(stats))
    if chart_path:
        png = render_stats_chart(stats)
        if png is None:
            print("Kein Diagramm: keine Daten oder matplotlib nicht verfügbar.")
        else:
            Path(chart_path).write_bytes(png)
            print(f"Diagramm gespeichert: {chart_path}")


def run_scout(cfg: Config, *, dry_run: bool = False) -> int:
    """Führt Bandcamp- und Beatport-Scout aus.

    Liest Tracks über fetch_bandcamp_feeds und fetch_beatport_charts, markiert
    Duplikate über Collection.mark_duplicates, schreibt Nicht-Duplikate in
    store_items (außer bei dry_run=True) und gibt die Anzahl der geschriebenen
    Store-Items zurück.

    Bei einem Fehler in einem Scout wird eine kurze Telegram-Meldung über
    output.telegram_call gesendet (nicht bei dry_run=True). Ein Fehler in einem
    Scout verhindert den anderen nicht.

    Nach dem Lauf (nur bei dry_run=False) wird ein Telegram-Block
    „🛒 Neu bei Bandcamp/Beatport" mit den in diesem Lauf neu in store_items
    geschriebenen Tracks gesendet (höchstens 5 Einträge).

    Args:
        cfg: Config mit scout- und download-Abschnitt
        dry_run: Wenn True, nur Anzeigen was geschrieben würde, keine DB-Änderung

    Returns:
        Anzahl der geschriebenen Store-Items (bei dry_run: 0).
    """
    scout_cfg = cfg.raw.get("scout", {}) or {}
    feed_urls = list((scout_cfg.get("bandcamp", {}) or {}).get("feeds", []) or [])
    chart_urls = list((scout_cfg.get("beatport", {}) or {}).get("charts", []) or [])

    # Jeder Scout läuft getrennt: ein Fehler darf den anderen nicht verhindern.
    # Eine Fehlermeldung wird – außer bei dry_run – an Telegram gemeldet; ein
    # Sendefehler (TelegramError) bricht run_scout nicht ab, wird aber geloggt.
    tracks: list[Track] = []
    for name, fn, urls in (
        ("Bandcamp", fetch_bandcamp_feeds, feed_urls),
        ("Beatport", fetch_beatport_charts, chart_urls),
    ):
        try:
            tracks.extend(fn(urls))
        except Exception as exc:  # noqa: BLE001 – ein Scout darf den anderen nicht stoppen
            log.warning("%s-Scout fehlgeschlagen: %s", name, exc)
            if not dry_run:
                try:
                    send_scout_error(cfg, name, str(exc))
                except TelegramError as terr:
                    log.warning("Telegram-Meldung zum %s-Scout fehlgeschlagen: %s", name, terr)

    # Duplikat-Abgleich gegen die lokale Sammlung. Fehlt der Ordner, warnt Collection
    # und findet nichts. Duplikate werden nur gezählt/geloggt, nicht gespeichert.
    coll = Collection(cfg["download"]["collection_dir"])
    coll.mark_duplicates(tracks)
    duplicates = [t for t in tracks if t.duplicate_of]
    fresh = [t for t in tracks if not t.duplicate_of]
    for t in duplicates:
        log.info("Scout: Duplikat übersprungen: %s – %s (vorhanden als %s)",
                 t.artist, t.title, t.duplicate_of)

    if dry_run:
        for t in fresh:
            log.info("[dry-run] Store-Item: %s – %s (%s)", t.artist, t.title, t.purchase_url)
        log.info("Scout: %d Duplikate übersprungen, %d Store-Items (dry-run, nicht geschrieben)",
                 len(duplicates), len(fresh))
        return 0

    written = 0
    new_items: list[dict[str, Any]] = []
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        # Vor dem Schreiben merken, welche sc_ids schon in store_items stehen, um
        # nur die in diesem Lauf neuen Einträge in den Digest-Block zu nehmen.
        existing_ids = {it["sc_id"] for it in db.get_store_items()}
        for t in fresh:
            db.upsert_store_item(
                sc_id=t.id,
                title=t.title,
                artist=t.artist,
                purchase_url=t.purchase_url or t.url,
                purchase_title=t.purchase_title,
            )
            written += 1
            if t.id not in existing_ids:
                new_items.append({
                    "sc_id": t.id,
                    "artist": t.artist,
                    "title": t.title,
                    "purchase_url": t.purchase_url or t.url,
                    "purchase_title": t.purchase_title,
                })
    log.info("Scout: %d Store-Items geschrieben, %d Duplikate übersprungen", written, len(duplicates))
    if new_items:
        try:
            send_scout_digest(cfg, new_items, max_items=5)
        except TelegramError as terr:
            log.warning("Telegram-Digest nach dem Scout fehlgeschlagen: %s", terr)
    return written


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

    aud = sub.add_parser("audit", parents=[common],
                         help="Bestehende Sammlung auf Fakes/BPM/Key prüfen (read-only)")
    aud.add_argument("--path", help="Pfad zur Sammlung (Standard: aus config.yaml)")
    aud.add_argument("--report", help="Dateipfad für den HTML-Report (z.B. report.html)")
    aud.add_argument("--force", action="store_true",
                     help="Alle Dateien neu analysieren (inkrementellen Cache ignorieren)")

    sub.add_parser("rekordbox", parents=[common], help="Rekordbox-XML der Inbox neu schreiben")

    sub.add_parser("intake", parents=[common],
                   help="Manuell abgelegte Tracks prüfen, analysieren und einsortieren")

    st = sub.add_parser("stats", parents=[common],
                        help="Wochenstatistik auf der Konsole, optional Diagramm und Telegram")
    st.add_argument("--days", type=int, default=7, help="Zeitfenster in Tagen (Standard 7)")
    st.add_argument("--chart", help="Diagramm als PNG in diese Datei schreiben")
    st.add_argument("--send", action="store_true", help="Statistik zusätzlich per Telegram senden")

    sub.add_parser("scout", parents=[common],
                   help="Bandcamp-Feeds und Beatport-Charts in store_items schreiben")

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
        elif a.mode == "audit":
            run_audit(
                cfg,
                path=a.path,
                report_path=a.report,
                force=a.force,
                dry_run=a.dry_run,
                no_telegram=a.no_telegram,
            )
        elif a.mode == "rekordbox":
            write_rekordbox_xml(cfg)
        elif a.mode == "intake":
            lines = run_intake(cfg, dry_run=a.dry_run)
            if lines:
                text = "\n".join(lines)
                if a.dry_run or a.no_telegram:
                    print(text)
                else:
                    send_telegram(cfg, text)
            if not a.dry_run:
                write_rekordbox_xml(cfg)
        elif a.mode == "stats":
            run_stats(cfg, days=a.days, chart_path=a.chart, send=a.send,
                      dry_run=a.dry_run, no_telegram=a.no_telegram)
        elif a.mode == "scout":
            run_scout(cfg, dry_run=a.dry_run)
        else:
            run_discover(cfg, a.dry_run, a.no_telegram)
    except Exception:
        # Über logging statt Python-Standard-Traceback: nur so greift die Token-Bereinigung
        log.exception("Lauf fehlgeschlagen")
        sys.exit(1)


if __name__ == "__main__":
    cli()
