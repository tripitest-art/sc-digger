from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

import sqlite3


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
    cutoff_date = datetime.now() - timedelta(days=days)
    
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