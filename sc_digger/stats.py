from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

import html
import logging
import sqlite3

from .db import TrackDB
from .models import Config
from .output import TelegramError, send_telegram_photo
from .trends import calculate_artist_trends, calculate_track_growth, format_trend_report

log = logging.getLogger(__name__)


@dataclass
class DigestStats:
    days: int
    runs_total: int = 0
    runs_ok: int = 0
    tracks_scanned: int = 0
    tracks_inbox: int = 0
    tracks_rejected: int = 0
    quality_breakdown: Dict[str, int] = field(default_factory=dict)
    likes: int = 0
    dislikes: int = 0
    later: int = 0
    top_artists: List[Tuple[str, int]] = field(default_factory=list)


def calculate_stats(
    track_db_path: str | Path,
    state_db_path: str | Path,
    *,
    days: int = 7,
) -> DigestStats:
    """Berechnet aggregierte Kennzahlen aus track_db (tracks, sc_feedback) und state_db (runs).
    Fehlen die DB-Dateien oder Tabellen, liefert die Funktion Nullen/leere Listen ohne Exception."""
    
    stats = DigestStats(days=days)
    
    # Zeitfenster berechnen
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        # State DB (runs) auswerten
        with sqlite3.connect(state_db_path) as db:
            # Prüfe, ob die Tabelle existiert
            cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='runs'")
            if not cursor.fetchone():
                return stats
                
            # Berechne Runs-Statistiken
            rows = db.execute("""
                SELECT mode, ok, found 
                FROM runs 
                WHERE finished_at > ? 
                AND mode = 'discover'
            """, (cutoff_date,))
            
            for row in rows:
                stats.runs_total += 1
                if row[1]:  # ok
                    stats.runs_ok += 1
                stats.tracks_scanned += row[2] if row[2] is not None else 0
                
    except sqlite3.Error:
        # Bei Fehlern wird einfach die leere Statistik zurückgegeben
        pass
    
    try:
        # Track DB auswerten
        with sqlite3.connect(track_db_path) as db:
            # Prüfe, ob die Tabellen existieren
            cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tracks'")
            if not cursor.fetchone():
                return stats
                
            cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sc_feedback'")
            if not cursor.fetchone():
                return stats
            
            # Inbox Tracks zählen
            rows = db.execute("""
                SELECT COUNT(*) 
                FROM tracks 
                WHERE status = 'inbox' 
                AND created_at > ?
            """, (cutoff_date,))
            
            stats.tracks_inbox = rows.fetchone()[0]
            
            # Rejected Tracks zählen
            rows = db.execute("""
                SELECT COUNT(*) 
                FROM tracks 
                WHERE status = 'rejected' 
                AND created_at > ?
            """, (cutoff_date,))
            
            stats.tracks_rejected = rows.fetchone()[0]
            
            # Quality-Statistiken
            rows = db.execute("""
                SELECT quality_status, COUNT(*) 
                FROM tracks 
                WHERE status = 'rejected' 
                AND created_at > ?
                GROUP BY quality_status
            """, (cutoff_date,))
            
            for row in rows:
                stats.quality_breakdown[row[0]] = row[1]
            
            # Top Artists ermitteln
            rows = db.execute("""
                SELECT artist, COUNT(*) as count
                FROM tracks 
                WHERE status = 'inbox' 
                AND created_at > ?
                GROUP BY artist
                ORDER BY count DESC
                LIMIT 10
            """, (cutoff_date,))
            
            stats.top_artists = [(row[0], row[1]) for row in rows]
            
            # Feedback zählen
            rows = db.execute("""
                SELECT value, COUNT(*) 
                FROM sc_feedback 
                WHERE updated_at > ?
                GROUP BY value
            """, (cutoff_date,))
            
            for row in rows:
                if row[0] == 'like':
                    stats.likes = row[1]
                elif row[0] == 'dislike':
                    stats.dislikes = row[1]
                elif row[0] == 'later':
                    stats.later = row[1]
                    
    except sqlite3.Error:
        # Bei Fehlern wird einfach die leere Statistik zurückgegeben
        pass
    
    return stats


def format_stats(stats: DigestStats) -> str:
    """Formatiert die Statistik als lesbaren Klartext für Telegram / Konsole."""

    text = f"Statistiken der letzten {stats.days} Tage:\n\n"

    text += f"✅ erfolgreiche Runs: {stats.runs_ok}/{stats.runs_total}\n"
    text += f"📊 gescannte Tracks: {f'{stats.tracks_scanned:,}'.replace(',', '.')}\n"
    text += f"📥 neue Inbox-Downloads: {stats.tracks_inbox}\n"
    text += f"🚫 abgelehnte Fakes: {stats.tracks_rejected}\n\n"

    if stats.quality_breakdown:
        text += "🔍 Qualitätsverteilung:\n"
        for quality, count in sorted(stats.quality_breakdown.items()):
            text += f"  {quality}: {count}\n"
        text += "\n"

    text += f"👍 Likes: {stats.likes} 👍\n"
    text += f"👎 Dislikes: {stats.dislikes}\n"
    text += f"🕒 Later: {stats.later}\n\n"

    if stats.top_artists:
        text += "🎵 Top-Artisten:\n"
        for artist, count in stats.top_artists:
            text += f"  {artist} ({count})\n"

    return text


def render_stats_chart(stats: DigestStats) -> bytes | None:
    """PNG-Balkendiagramm der Wochenzahlen (gescannt, Inbox, abgelehnt, Likes, Dislikes).

    None, wenn es keine Daten gibt oder matplotlib nicht verfügbar ist. matplotlib wird
    erst hier importiert (Agg-Backend, kein Display nötig); der Import ist optional, damit
    der Rest des Tools ohne die Bibliothek läuft. Die Funktion wirft nie.
    """
    if stats.runs_total == 0 and stats.tracks_scanned == 0:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        log.warning("matplotlib nicht verfügbar, kein Diagramm: %s", e)
        return None

    fig = None
    try:
        from io import BytesIO

        labels = ["gescannt", "Inbox", "abgelehnt", "Likes", "Dislikes"]
        values = [stats.tracks_scanned, stats.tracks_inbox, stats.tracks_rejected,
                  stats.likes, stats.dislikes]
        fig, ax = plt.subplots(figsize=(6, 3))
        ax.bar(labels, values)
        ax.set_title(f"Woche im Überblick ({stats.days} Tage)")
        ax.set_ylabel("Anzahl")
        fig.tight_layout()
        buf = BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        return buf.getvalue()
    except Exception as e:
        log.warning("Statistik-Diagramm fehlgeschlagen: %s", e)
        return None
    finally:
        if fig is not None:
            plt.close(fig)


def _exploration_section(cfg: Config, days: int = 30) -> str:
    """Vorschlagszeilen für erfolgreiche Exploration-Tags; leer ohne Treffer oder bei Fehlern."""
    try:
        with TrackDB(cfg["state"]["track_db_path"]) as db:
            top = db.top_exploration_tags(days=days)
    except Exception as e:
        log.warning("Exploration-Vorschläge nicht verfügbar: %s", e)
        return ""
    if not top:
        return ""
    lines = [f"🔍 `{html.escape(t['tag'])}` hat in den letzten {days} Tagen {t['successes']}× "
             f"erfolgreiche Treffer geliefert → `search.tags` ergänzen?" for t in top]
    return "\n\n" + "\n".join(lines)


def _trend_section(cfg: Config, days: int = 7) -> str:
    """Trend-Radar-Text für den Wochen-Digest; leer, wenn deaktiviert oder keine Trends.

    Liest digest.trend_radar (Default True) und digest.trend_radar_days (Default 7)
    aus der Config. Ruft calculate_artist_trends und calculate_track_growth mit
    min_initial_likes=10 und limit=10 auf. Formatiert das Ergebnis via format_trend_report.
    Bei jeder Exception wird die Meldung geloggt und ein leerer String zurückgegeben,
    nie eine Exception geworfen.
    """
    try:
        digest_cfg = cfg.raw.get("digest") or {}
        if not bool(digest_cfg.get("trend_radar", True)):
            return ""
        trend_days = int(digest_cfg.get("trend_radar_days", 7))
        track_db_path = cfg["state"]["track_db_path"]
        artist_trends = calculate_artist_trends(
            track_db_path, days=trend_days, min_initial_likes=10, limit=10
        )
        track_trends = calculate_track_growth(
            track_db_path, days=trend_days, min_initial_likes=10, limit=10
        )
        if not artist_trends and not track_trends:
            return ""
        report = format_trend_report(artist_trends, track_trends, days=trend_days)
        return "\n\n" + report
    except Exception as e:
        log.warning("Trend-Radar nicht verfügbar: %s", e)
        return ""


def send_weekly_digest(
    cfg: Config,
    *,
    days: int = 7,
    chart: bool | None = None,
    dry_run: bool = False,
    no_telegram: bool = False,
    chat_id: str | None = None,
) -> DigestStats:
    """Berechnet die Wochenstatistik und schickt sie als „📊 Woche im Überblick“.

    dry_run/no_telegram: nur auf der Konsole, kein Rendern und kein Telegram-Aufruf.
    chart None: aus der Config (digest.stats_chart, Standard aus). chart True hängt ein
    Balkendiagramm an. Ein Telegram-Fehler wird geloggt, aber nie geworfen: der tägliche
    Lauf soll daran nicht scheitern.

    Hängt zusätzlich den Trend-Radar an, wenn cfg["digest"]["trend_radar"] True ist
    (Default: True). Die Trend-Tage stammen aus cfg["digest"]["trend_radar_days"] (Default: 7).
    """
    stats = calculate_stats(cfg["state"]["track_db_path"], cfg["state"]["db_path"], days=days)
    text = "📊 Woche im Überblick\n\n" + format_stats(stats)
    text += _exploration_section(cfg)
    text += _trend_section(cfg, days=days)
    if dry_run or no_telegram:
        print(text)
        return stats
    if chart is None:
        chart = bool((cfg.raw.get("digest") or {}).get("stats_chart", False))
    png = render_stats_chart(stats) if chart else None
    try:
        send_telegram_photo(cfg, png, text, chat_id=chat_id)
    except TelegramError as e:
        log.warning("Wochen-Digest konnte nicht gesendet werden: %s", e)
    return stats
