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
import inspect
import logging
import os
import re
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path

from .db import TrackDB
from .harmonic import compatible_keys, find_mix_candidates, format_mix_list
from .main import run_link
from .models import Config
from .output import TelegramError, parse_feedback_callback, telegram_call, telegram_download
from .output import State, send_telegram_voice
from . import output, preview, rekordbox
from .cleanup import audit_inbox_duplicates, format_duplicate_report
from .fingerprint import compute_fingerprint
from .preview import extract_preview_from_url
from .redact import install_redacting_logging
from .similarity import SIMILAR_FAILED, build_similar_reply
from .soundcloud import SoundCloudClient, SoundCloudError
from .stats import calculate_stats, format_stats

log = logging.getLogger("sc_digger.bot")

URL_RE = re.compile(r"https?://(?:on\.)?(?:www\.|m\.)?soundcloud\.com/\S+", re.I)

MIX_USAGE = "Aufruf: /mix <Camelot-Key> <BPM> [Toleranz], z. B. /mix 5A 155 oder /mix 8B 160 2"
STATS_USAGE = "Aufruf: /stats [Tage (1–365, Standard: 7)]"
PREVIEW_USAGE = "Aufruf: /preview <Suchtext oder id oder sc:<SoundCloud-ID>>"
SIMILAR_USAGE = "Aufruf: /similar <SoundCloud-Link>"
REJECTED_USAGE = "Aufruf: /rejected [Anzahl (1–100, Standard: 10)]"
RETRY_USAGE = "Aufruf: /retry <id>"

# Länge des temporären Snippets für die /similar-Suche: SoundCloud-Stream-Previews sind
# rund 30 s lang, mehr käme aus der Download-Quelle ohnehin nicht.
SIMILAR_FETCH_DURATION_S = 30.0

SYNC_XML_DISABLED = "Rekordbox-XML ist abgeschaltet (rekordbox.xml_enabled)."
SYNC_XML_FAILED = "Rekordbox-XML konnte nicht geschrieben werden, siehe Container-Log."

HELP_TEXT = (
    "sc-digger Bot\n\n"
    "Schick mir einen SoundCloud-Link oder eine Audiodatei:\n"
    "• Playlist/Set-Link -> ich zeige dir alle Tracks der Playlist\n"
    "• einzelner Track-Link -> ich zeige dir die 'Station' dazu "
    "(das, was der SoundCloud-Algorithmus als Radio vorschlägt)\n"
    "• Audio-/Sprachnachricht -> die Top-5 ähnlichen Aufnahmen aus der Sammlung\n\n"
    "Befehle:\n"
    "• /kaufliste -> zeigt die aktuelle Kaufliste offener Store-Tracks\n"
    "• /curator_mining -> Profile aus 👍-Tracks vorschlagen\n"
    "• /mix 5A 155 -> harmonisch passende Tracks aus der Sammlung (±3 BPM)\n"
    "• /preview &lt;Suchtext oder id&gt; -> 20s-Snippet eines heruntergeladenen Tracks als Sprachnachricht\n"
    "• /preview sc:&lt;SoundCloud-ID&gt; -> 20s-Snippet direkt aus dem SoundCloud-Stream (nicht heruntergeladen)\n"
    "• /similar &lt;SoundCloud-Link&gt; -> Top-5 ähnliche Aufnahmen aus der Sammlung\n"
    "• /stats [Tage] -> Statistiken über Scans, Inbox und Feedback\n"
    "• /rejected [Anzahl] -> zuletzt abgelehnte Downloads aus der Quarantäne\n"
    "• /retry &lt;id&gt; -> abgelehnten Track zur erneuten Prüfung freigeben\n"
    "• /cleanup_inbox -> Dubletten in der Inbox auflisten (nur lesend)\n"
    "• /sync_xml -> Rekordbox-XML der Inbox sofort neu schreiben\n\n"
    "Kein täglicher Filter, du bekommst die volle Liste mit Stats und Download-Einordnung."
)


def _send_text(cfg: Config, chat_id: str, text: str) -> None:
    """Sendet text per sendMessage an chat_id, zunächst mit parse_mode='HTML'.
    Scheitert Telegram am HTML (Status 400 bzw. Entity-Parsing-Fehler), wird die Nachricht
    ohne parse_mode als reiner Text erneut gesendet, damit der Bot nie stumm bleibt."""
    try:
        telegram_call(cfg.telegram_token, "sendMessage",
                      json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True})
    except TelegramError as e:
        if "400" not in str(e) and "parse entities" not in str(e):
            raise
        log.warning("HTML-Versand fehlgeschlagen (%s), sende als Klartext erneut", e)
        telegram_call(cfg.telegram_token, "sendMessage",
                      json={"chat_id": chat_id, "text": text,
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


def preview_stream_reply(cfg: Config, sc_id: int) -> str | None:
    """Stream-Preview für einen SoundCloud-Track senden. Wirft nie.

    Aktiv nur, wenn cfg preview.stream_enabled. Bei Erfolg wird send_telegram_voice
    mit einer temporären OGG-Datei aufgerufen (tempfile, danach immer löschen) und
    None zurückgegeben. Sonst eine Klartext-Fehlermeldung: deaktiviert, kein
    Stream-Preview verfügbar, Verarbeitung fehlgeschlagen. Keine Exception in den
    Bot-Loop, keine Secrets in den Texten.
    """
    try:
        if not cfg["preview"]["stream_enabled"]:
            return "Stream-Preview ist deaktiviert (preview.stream_enabled: false)."

        try:
            stream_url = SoundCloudClient().preview_url(int(sc_id))
        except Exception as e:
            log.warning("Stream-Preview-URL für %s nicht ermittelbar: %s", sc_id, e)
            return "Stream-Preview konnte nicht ermittelt werden."

        if not stream_url:
            return f"kein Stream-Preview für SoundCloud-ID {sc_id} verfügbar."

        out_ogg = Path(tempfile.gettempdir()) / f"sc-digger-stream-{int(sc_id)}.ogg"
        try:
            if not extract_preview_from_url(
                stream_url, out_ogg, start_s=0.0, duration_s=20.0, bitrate_kbps=64
            ):
                return "Stream-Preview konnte nicht erstellt werden (ffmpeg-Fehler)."
            send_telegram_voice(cfg, out_ogg, caption=f"SoundCloud {sc_id}")
            return None
        except TelegramError as e:
            return f"Stream-Preview konnte nicht gesendet werden: {e}."
        finally:
            try:
                if out_ogg.is_file():
                    out_ogg.unlink()
            except OSError:
                pass
    except Exception:
        log.exception("Unerwarteter Fehler bei der Stream-Preview")
        return "Stream-Preview fehlgeschlagen, siehe Container-Log."


def preview_reply(cfg: Config, text: str, chat_id: str | None = None) -> str | None:
    """Antwort auf '/preview <Argument>'. Muster: stats_reply. Sendet NIE selbst
    Textnachrichten; Rückgabe ist der Text, den handle_message per _send_text
    schickt, oder None, wenn die Voice-Message bereits gesendet wurde.
    1. Fehlendes Argument -> PREVIEW_USAGE. (Alles außer reinen Ziffern ist
       Suchtext; reine Ziffern sind die lokale Track-id.)
    2. Reine Ziffern -> lokale Track-id: TrackDB.get_track_by_id(int).
       Kein Treffer -> "Kein Track mit id <id> in der Track-DB."
    3. Sonst Suchtext: TrackDB.search_tracks(arg). Kein Treffer ->
       "Kein Treffer für '<arg>'."; mehrere Treffer -> Liste mit max. 5 Zeilen
       "<id>: <artist> – <title>", bei mehr als 5 Treffern die Zeile
       "… und weitere, bitte genauer suchen"; Hinweis: "Mit /preview <id>
       senden." (id = lokale Track-DB-id.) Genau ein Treffer -> weiter mit 4.
    4. Datei (TrackRecord.path) existiert nicht ->
       "Datei nicht gefunden: <pfad>."
    5. extract_preview(input_path, out_ogg, start_s=None, duration_s=20.0,
       bitrate_kbps=64) -> False (Zieldatei in tempfile.gettempdir(),
       Aufräumen im finally) -> "Preview konnte nicht erstellt werden
       (ffmpeg-Fehler)."
    6. Erfolg: send_telegram_voice(cfg, out_ogg,
       caption="<artist> – <title>", chat_id=chat_id), danach temporäre
       OGG-Datei löschen; Rückgabe None.
       TelegramError beim Versand -> "Preview konnte nicht gesendet werden:
       <Fehlermeldung ohne Token>."
    Wirft in keinem Fall (keine Exception an den Bot-Loop)."""
    try:
        parts = text.strip().split()
        arg = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
        if not arg:
            return PREVIEW_USAGE

        if arg.startswith("sc:"):
            digits = arg[3:]
            if not digits.isdigit():
                return PREVIEW_USAGE
            return preview_stream_reply(cfg, int(digits))

        track_db_path = cfg["state"]["track_db_path"]
        try:
            with TrackDB(track_db_path) as db:
                if arg.isdigit():
                    rec = db.get_track_by_id(int(arg))
                    recs = [rec] if rec is not None else []
                else:
                    recs = db.search_tracks(arg)
        except Exception as e:
            log.warning("Preview-Suche fehlgeschlagen für %r: %s", arg, e)
            return "Preview konnte nicht erstellt werden (Datenbankfehler)."

        if arg.isdigit() and not recs:
            return f"Kein Track mit id {arg} in der Track-DB."
        if not recs:
            return f"Kein Treffer für '{arg}'."
        if len(recs) > 1:
            lines = [f"{rec.id}: {rec.artist} – {rec.title}" for rec in recs[:5]]
            if len(recs) > 5:
                lines.append("… und weitere, bitte genauer suchen")
            lines.append("Mit /preview <id> senden.")
            return "\n".join(lines)

        rec = recs[0]
        audio_path = Path(rec.path)
        if not audio_path.is_file():
            return f"Datei nicht gefunden: {rec.path}."

        out_ogg = Path(tempfile.gettempdir()) / f"sc-digger-preview-{rec.id}.ogg"
        try:
            prev_cfg = cfg.raw.get("preview") or {}
            duration_s = float(prev_cfg.get("duration_s", 20.0))
            riser_s = float(prev_cfg.get("riser_s", 5.0))
            kwargs = {
                "start_s": None,
                "duration_s": duration_s,
                "bitrate_kbps": 64,
            }
            sig = inspect.signature(preview.extract_preview)
            if "riser_s" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            ):
                kwargs["riser_s"] = riser_s

            ok = preview.extract_preview(audio_path, out_ogg, **kwargs)
            if not ok:
                return "Preview konnte nicht erstellt werden (ffmpeg-Fehler)."
            try:
                output.send_telegram_voice(
                    cfg, out_ogg, caption=f"{rec.artist} – {rec.title}", chat_id=chat_id
                )
            except TelegramError as e:
                return f"Preview konnte nicht gesendet werden: {e}."
            return None
        finally:
            try:
                if out_ogg.is_file():
                    out_ogg.unlink()
            except OSError:
                pass
    except Exception:
        log.exception("Unerwarteter Fehler bei /preview")
        return "Preview fehlgeschlagen, siehe Container-Log."


def _fetch_audio_via_soundcloud(url: str, dest: Path) -> Path:
    """Standard-Audiobeschaffung für /similar: Track auflösen, Stream-Quelle holen und
    daraus ein temporäres OGG an dest schreiben. Nutzt die vorhandenen Helfer
    (SoundCloudClient, preview.extract_preview_from_url), lädt also nichts in die Sammlung.
    Wirft bei Fehlern; der Aufrufer loggt und antwortet mit dem Standard-Fehlertext."""
    sc = SoundCloudClient()
    track = sc.resolve_track(url)
    stream_url = sc.preview_url(int(track.id))
    if not stream_url:
        raise SoundCloudError(f"keine Stream-Quelle für {url}")
    if not extract_preview_from_url(
        stream_url, dest, start_s=0.0, duration_s=SIMILAR_FETCH_DURATION_S, bitrate_kbps=64
    ):
        raise RuntimeError(f"Audio für {url} nicht extrahierbar")
    return dest


def _compute_fingerprint_values(path: Path) -> list[int] | None:
    """Fingerprint-Rohwerte einer Datei als Liste oder None. Die Fehlerdetails (fpcalc
    fehlt, kaputtes Audio) loggt bereits fingerprint.compute_fingerprint."""
    fp = compute_fingerprint(Path(path))
    return list(fp.values) if fp is not None else None


def similar_reply_from_url(
    cfg: Config,
    db: TrackDB,
    url: str,
    *,
    fetch_audio=None,
    compute_fp=None,
) -> str:
    """„Mehr davon“ zu einem SoundCloud-Link: Audio temporär besorgen, Fingerprint rechnen,
    Top-5 ähnliche Aufnahmen als Text liefern (gleiche Antwort wie bei einer Datei).

    fetch_audio(url, dest_path) und compute_fp(path) sind injizierbar (Tests); ohne Übergabe
    laufen die Standard-Helfer _fetch_audio_via_soundcloud und _compute_fingerprint_values.
    Bei Download-/Fingerprint-Fehler: loggen und SIMILAR_FAILED (kein Raise). Die temporäre
    Datei wird in jedem Fall entfernt, damit der Container nicht vollläuft.

    cfg bleibt in der Signatur (Issue-Schnittstelle, einheitlich zu den anderen Bot-Antworten),
    die Standard-Helfer kommen ohne Konfiguration aus.
    """
    fetch = fetch_audio if fetch_audio is not None else _fetch_audio_via_soundcloud
    compute = compute_fp if compute_fp is not None else _compute_fingerprint_values

    tmp: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix="sc-digger-similar-", suffix=".audio")
        os.close(fd)
        tmp = Path(tmp_name)
        fetch(url, tmp)
        values = compute(tmp)
        if not values:
            log.warning("Kein Fingerprint für %s (Quelle %s)", tmp, url)
            return SIMILAR_FAILED
        return build_similar_reply(db, list(values), top_n=5)
    except Exception:
        log.exception("Ähnliche Aufnahmen zu %s nicht ermittelbar", url)
        return SIMILAR_FAILED
    finally:
        if tmp is not None:
            try:
                if tmp.is_file():
                    tmp.unlink()
            except OSError:
                pass


def _download_telegram_audio(cfg: Config, file_id: str, dest: Path) -> Path:
    """Lädt eine von Telegram gesendete Audiodatei nach dest.

    Erst getFile über `output.telegram_call` (Bot-Methode), dann die Rohbytes über
    `output.telegram_download`. Beide Wege werfen TelegramError ohne Token in der Meldung.
    """
    info = telegram_call(cfg.telegram_token, "getFile", http="get", params={"file_id": file_id})
    file_path = (info.get("result") or {}).get("file_path")
    if not file_path:
        raise TelegramError("getFile: Telegram lieferte keinen file_path")
    return telegram_download(cfg.telegram_token, file_path, dest)


def similar_reply_from_audio(cfg: Config, db: TrackDB, audio_path: Path) -> str:
    """„Mehr davon“ zu einer an den Bot gesendeten Audio-Datei.

    Fingerprint über den Standard-Helfer (_compute_fingerprint_values -> compute_fingerprint),
    danach dieselbe Antwort wie beim Link-Weg (build_similar_reply). Jeder Fehler (faules
    Audio, fpcalc fehlt, DB nicht lesbar) wird geloggt und als SIMILAR_FAILED geliefert;
    die Funktion wirft nie.
    """
    try:
        values = _compute_fingerprint_values(Path(audio_path))
        if not values:
            log.warning("Kein Fingerprint für gesendete Datei %s", audio_path)
            return SIMILAR_FAILED
        return build_similar_reply(db, list(values), top_n=5)
    except Exception:
        log.exception("Ähnliche Aufnahmen für gesendete Datei %s nicht ermittelbar", audio_path)
        return SIMILAR_FAILED


def handle_audio_message(cfg: Config, chat_id: str, audio: dict) -> None:
    """Reagiert auf eine Audio-/Sprachnachricht: Datei holen, Fingerprint, Top-5 als Text.

    Lädt die Datei in eine temporäre Datei (wird immer wieder gelöscht, der Container darf
    nicht volllaufen), öffnet die Track-DB und schickt die Antwort über `_send_text`.
    Ohne file_id passiert nichts; jeder Fehler wird geloggt und als SIMILAR_FAILED
    beantwortet. Wirft nie.
    """
    file_id = (audio or {}).get("file_id")
    if not file_id:
        return

    tmp: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix="sc-digger-audio-", suffix=".audio")
        os.close(fd)
        tmp = Path(tmp_name)
        _download_telegram_audio(cfg, file_id, tmp)
        with TrackDB(cfg["state"]["track_db_path"]) as db:
            reply = similar_reply_from_audio(cfg, db, tmp)
        _send_text(cfg, chat_id, html.escape(reply, quote=False))
    except Exception:
        log.exception("Fehler bei gesendeter Audio-Datei")
        _send_text(cfg, chat_id, SIMILAR_FAILED)
    finally:
        if tmp is not None:
            try:
                if tmp.is_file():
                    tmp.unlink()
            except OSError:
                pass


def sync_xml_reply(cfg: Config, now: datetime | None = None) -> str:
    """Schreibt die Rekordbox-XML über rekordbox.write_rekordbox_xml(cfg) neu und gibt die Antwort zurück.

    - Fehlt cfg["rekordbox"] oder ist xml_enabled falsch: SYNC_XML_DISABLED, es wird nichts geschrieben.
    - write_rekordbox_xml liefert None (z. B. Ziel in der Sammlung): SYNC_XML_FAILED.
    - Sonst die Zahl der Tracks aus dem Attribut Entries des Knotens COLLECTION der geschriebenen Datei
      lesen und f"✅ Rekordbox-XML neu geschrieben: {n} Tracks ({stamp})" zurückgeben,
      stamp = (now or datetime.now()).strftime("%d.%m.%Y %H:%M"). Immer "Tracks", auch bei 0 oder 1.
    - Jede Ausnahme (auch beim Lesen der Datei): log.exception, SYNC_XML_FAILED. Wirft nie.
    """
    try:
        rb_cfg = cfg.raw.get("rekordbox") if isinstance(cfg.raw, dict) else None
        if not rb_cfg or not isinstance(rb_cfg, dict) or not rb_cfg.get("xml_enabled"):
            return SYNC_XML_DISABLED

        xml_path = rekordbox.write_rekordbox_xml(cfg)
        if xml_path is None:
            return SYNC_XML_FAILED

        root = ET.parse(xml_path).getroot()
        collection = root.find("COLLECTION")
        n = collection.get("Entries") if collection is not None else None
        stamp = (now or datetime.now()).strftime("%d.%m.%Y %H:%M")
        return f"✅ Rekordbox-XML neu geschrieben: {n} Tracks ({stamp})"
    except Exception:
        log.exception("Fehler beim Neuschreiben der Rekordbox-XML")
        return SYNC_XML_FAILED


def cleanup_inbox_reply(cfg: Config) -> str:
    """Antwort auf '/cleanup_inbox'. Read-only Übersicht über Dubletten in der Inbox.
    Liest inbox_dir aus cfg["download"], scannt via audit_inbox_duplicates und
    formatiert via format_duplicate_report.
    Bei Textlänge > 3800 Zeichen wird gekürzt mit Hinweis auf CLI 'cleanup-inbox'.
    Wirft nie."""
    try:
        dl_cfg = cfg.raw.get("download") if isinstance(cfg.raw, dict) else None
        inbox_dir = dl_cfg.get("inbox_dir") if isinstance(dl_cfg, dict) else None
        if not inbox_dir:
            return "Kein inbox_dir in config konfiguriert."
        inbox = Path(inbox_dir)
        groups = audit_inbox_duplicates(inbox)
        report = format_duplicate_report(groups, inbox=inbox)
        if len(report) > 3800:
            report = report[:3800] + "\n\n… gekürzt. Vollständige Liste per CLI: cleanup-inbox"
        return report
    except Exception:
        log.exception("Fehler beim /cleanup_inbox-Befehl")
        return "Inbox-Cleanup fehlgeschlagen, siehe Container-Log."


def rejected_reply(cfg: Config, text: str) -> str:
    """Antwort auf '/rejected [limit]' (1–100, Standard: 10). Ungültiges Argument -> REJECTED_USAGE."""
    parts = text.strip().split()
    limit = 10
    if len(parts) == 2:
        try:
            limit = int(parts[1])
        except ValueError:
            return REJECTED_USAGE
        if not (1 <= limit <= 100):
            return REJECTED_USAGE
    elif len(parts) > 2:
        return REJECTED_USAGE

    state_db_path = (cfg.raw.get("state") or {}).get("db_path")
    if not state_db_path:
        return "Quarantäne nicht konfiguriert (state.db_path fehlt)."

    with State(state_db_path) as state:
        items = state.list_rejected(limit)
    if not items:
        return "Keine abgelehnten Tracks in der Quarantäne."

    lines = [f"🚫 Abgelehnte Downloads ({len(items)}):"]
    for item in items:
        title = html.escape(item["title"] or "", quote=False)
        reason = html.escape(item["reason"] or "ohne Grund", quote=False)
        cutoff = f", {item['cutoff_hz'] / 1000:.0f} kHz" if item.get("cutoff_hz") else ""
        lines.append(f"• <code>{item['id']}</code> – {title}: {reason}{cutoff}")
    lines.append("")
    lines.append("Zurückholen: /retry &lt;id&gt;")
    return "\n".join(lines)


def retry_reply(cfg: Config, text: str) -> str:
    """Bearbeitet '/retry <id>': entfernt den Track aus Quarantäne und seen, damit der
    nächste Suchlauf ihn wieder als Neuentdeckung prüft. Ungültiges Argument -> RETRY_USAGE."""
    parts = text.strip().split()
    if len(parts) != 2:
        return RETRY_USAGE
    try:
        track_id = int(parts[1])
    except ValueError:
        return RETRY_USAGE
    if track_id <= 0:
        return RETRY_USAGE

    state_db_path = (cfg.raw.get("state") or {}).get("db_path")
    if not state_db_path:
        return "Quarantäne nicht konfiguriert (state.db_path fehlt)."

    with State(state_db_path) as state:
        deleted = state.unmark_rejected(track_id)
    if deleted:
        return (f"Track {track_id} zurückgesetzt und aus der Quarantäne freigegeben – "
                "wird beim nächsten Suchlauf neu geprüft.")
    return f"Track {track_id} ist nicht in der Quarantäne."


def handle_message(cfg: Config, sc: SoundCloudClient, chat_id: str, text: str) -> None:
    text = text.strip()
    # Erstes Wort bereinigt um einen angehängten Bot-Namen (/help@bot bzw. "/help @bot")
    first_word = text.split()[0].split("@")[0].lower() if text else ""
    if first_word in ("/start", "/help"):
        _send_text(cfg, chat_id, HELP_TEXT)
        return

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

    if first_word == "/stats":
        try:
            _send_text(cfg, chat_id, stats_reply(cfg, text))
        except Exception:
            log.exception("Fehler bei /stats-Befehl")
            _send_text(cfg, chat_id, "Statistikabfrage fehlgeschlagen, siehe Container-Log.")
        return

    if first_word == "/rejected":
        try:
            _send_text(cfg, chat_id, rejected_reply(cfg, text))
        except Exception:
            log.exception("Fehler bei /rejected-Befehl")
            _send_text(cfg, chat_id, "Quarantäne-Abfrage fehlgeschlagen, siehe Container-Log.")
        return

    if first_word == "/retry":
        try:
            _send_text(cfg, chat_id, retry_reply(cfg, text))
        except Exception:
            log.exception("Fehler bei /retry-Befehl")
            _send_text(cfg, chat_id, "Retry fehlgeschlagen, siehe Container-Log.")
        return

    if first_word == "/sync_xml":
        try:
            _send_text(cfg, chat_id, sync_xml_reply(cfg))
        except Exception:
            log.exception("Fehler beim /sync_xml-Befehl")
            _send_text(cfg, chat_id, "XML-Sync fehlgeschlagen, siehe Container-Log.")
        return

    if first_word == "/cleanup_inbox":
        try:
            _send_text(cfg, chat_id, cleanup_inbox_reply(cfg))
        except Exception:
            log.exception("Fehler beim /cleanup_inbox-Befehl")
            _send_text(cfg, chat_id, "Inbox-Cleanup fehlgeschlagen, siehe Container-Log.")
        return

    if first_word == "/preview":
        try:
            reply = preview_reply(cfg, text, chat_id=chat_id)
            if reply is not None:
                _send_text(cfg, chat_id, reply)
        except Exception:
            log.exception("Fehler bei /preview-Befehl")
            _send_text(cfg, chat_id, "Preview fehlgeschlagen, siehe Container-Log.")
        return

    if first_word == "/similar":
        parts = text.split(maxsplit=1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        m_similar = URL_RE.search(arg)
        if not m_similar:
            _send_text(cfg, chat_id, SIMILAR_USAGE)
            return
        try:
            with TrackDB(cfg["state"]["track_db_path"]) as db:
                reply = similar_reply_from_url(cfg, db, m_similar.group(0))
            _send_text(cfg, chat_id, html.escape(reply, quote=False))
        except Exception:
            log.exception("Fehler beim /similar-Befehl")
            _send_text(cfg, chat_id, SIMILAR_FAILED)
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
                elif msg.get("audio") or msg.get("voice"):
                    handle_audio_message(cfg, chat_id, msg.get("audio") or msg.get("voice"))
        except TelegramError as e:
            log.warning("Telegram-Polling-Fehler (%s), versuche erneut", e)
            time.sleep(5)
        except Exception:
            log.exception("Unerwarteter Fehler im Bot-Loop")
            time.sleep(5)


def stats_reply(cfg: Config, text: str) -> str:
    """Antwort auf '/stats [Tage]' (1–365 Tage, Standard: 7). Bei ungültigem Argument STATS_USAGE."""
    parts = text.strip().split()
    if len(parts) == 1:
        days = 7
    elif len(parts) == 2:
        try:
            days = int(parts[1])
            if not (1 <= days <= 365):
                return STATS_USAGE
        except ValueError:
            return STATS_USAGE
    else:
        return STATS_USAGE

    state_cfg = cfg.raw.get("state", {}) if isinstance(cfg.raw, dict) else {}
    track_db_path = state_cfg.get("track_db_path")
    state_db_path = state_cfg.get("db_path")
    if not track_db_path or not state_db_path:
        return "Statistikdaten nicht konfiguriert."

    stats = calculate_stats(track_db_path, state_db_path, days=days)
    return format_stats(stats)


def cli() -> None:
    install_redacting_logging(logging.INFO)
    listen(Config.load("config.yaml"))


if __name__ == "__main__":
    cli()
