"""SoundCloud-Trend-Radar (Phase 4): Wachstumsanalyse historischer Engagement-Snapshots.

Liest die Tabelle `track_snapshots` aus der Track-DB (`sc_digger/db.py`, Migration 4) und
ermittelt, welche Tracks und Artists über ein Zeitfenster die stärksten Like-Zuwächse hatten.
Das Zeitfenster ist relativ zum jüngsten vorhandenen Snapshot, nicht zur Wanduhr: Die Analyse
soll auch dann reproduzierbar sein, wenn der letzte Snapshot etwas zurückliegt (z. B. nach
einer Pause des täglichen Laufs).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


@dataclass
class TrackGrowth:
    sc_id: int
    artist: str
    title: str
    start_likes: int
    end_likes: int
    delta_likes: int
    growth_rate: float  # z. B. 2.0 für +200%


@dataclass
class ArtistGrowth:
    artist: str
    track_count: int
    start_likes: int
    end_likes: int
    delta_likes: int
    growth_rate: float


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _open(db_path: str | Path) -> sqlite3.Connection | None:
    """Öffnet die Track-DB read-only-ähnlich; None, wenn Datei oder Tabelle fehlt."""
    path = Path(db_path)
    if not path.exists():
        return None
    try:
        con = sqlite3.connect(str(path))
    except sqlite3.Error:
        return None
    con.row_factory = sqlite3.Row
    try:
        tables = {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    except sqlite3.Error:
        con.close()
        return None
    if "track_snapshots" not in tables:
        con.close()
        return None
    return con


def _cutoff(con: sqlite3.Connection, days: int) -> str | None:
    """Untergrenze des Fensters: jüngster Snapshot minus `days` Tage (None ohne Daten)."""
    row = con.execute("SELECT MAX(recorded_at) AS m FROM track_snapshots").fetchone()
    newest = _parse_ts(row["m"] if row else None)
    if newest is None:
        return None
    return (newest - timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")


def _snapshots(con: sqlite3.Connection, cutoff: str) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT sc_id, artist, title, likes, recorded_at
        FROM track_snapshots
        WHERE datetime(recorded_at) >= datetime(?)
        ORDER BY sc_id ASC, datetime(recorded_at) ASC, id ASC
        """,
        (cutoff,),
    ).fetchall()


def _by_track(rows: list[sqlite3.Row]) -> dict[int, list[sqlite3.Row]]:
    grouped: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        grouped.setdefault(int(r["sc_id"]), []).append(r)
    return grouped


def _growth_rate(delta: int, start: int) -> float:
    return delta / start if start > 0 else 0.0


def calculate_track_growth(
    track_db_path: str | Path,
    *,
    days: int = 7,
    min_initial_likes: int = 10,
    limit: int = 10,
) -> list[TrackGrowth]:
    """Berechnet Tracks mit dem stärksten Like-Wachstum über das Zeitfenster (letzte `days` Tage).

    Vergleicht den ältesten und neuesten Snapshot jedes Tracks innerhalb des Zeitfensters.
    Nur Tracks mit start_likes >= min_initial_likes und delta_likes > 0.
    growth_rate = delta_likes / start_likes.
    Sortiert primär nach growth_rate absteigend, sekundär nach delta_likes absteigend.
    Fehlen die DB-Datei oder Tabelle, liefert die Funktion eine leere Liste ohne Exception.
    """
    con = _open(track_db_path)
    if con is None:
        return []
    try:
        cutoff = _cutoff(con, days)
        if cutoff is None:
            return []
        grouped = _by_track(_snapshots(con, cutoff))
    except sqlite3.Error:
        return []
    finally:
        con.close()

    result: list[TrackGrowth] = []
    for sc_id, snaps in grouped.items():
        start, end = snaps[0], snaps[-1]
        start_likes = int(start["likes"] or 0)
        end_likes = int(end["likes"] or 0)
        delta = end_likes - start_likes
        if start_likes < min_initial_likes or delta <= 0:
            continue
        result.append(
            TrackGrowth(
                sc_id=sc_id,
                artist=str(end["artist"] or start["artist"] or ""),
                title=str(end["title"] or start["title"] or ""),
                start_likes=start_likes,
                end_likes=end_likes,
                delta_likes=delta,
                growth_rate=_growth_rate(delta, start_likes),
            )
        )
    result.sort(key=lambda g: (g.growth_rate, g.delta_likes), reverse=True)
    return result[:limit] if limit is not None else result


def calculate_artist_trends(
    track_db_path: str | Path,
    *,
    days: int = 7,
    min_initial_likes: int = 20,
    limit: int = 10,
) -> list[ArtistGrowth]:
    """Aggregiert das Wachstum aller Tracks eines Artists über das Zeitfenster.

    growth_rate = sum(delta_likes) / sum(start_likes).
    Nur Artists mit sum(start_likes) >= min_initial_likes und sum(delta_likes) > 0.
    Sortiert nach growth_rate absteigend, sekundär nach delta_likes absteigend.
    Fehlen die DB-Datei oder Tabelle, liefert die Funktion eine leere Liste ohne Exception.
    """
    con = _open(track_db_path)
    if con is None:
        return []
    try:
        cutoff = _cutoff(con, days)
        if cutoff is None:
            return []
        grouped = _by_track(_snapshots(con, cutoff))
    except sqlite3.Error:
        return []
    finally:
        con.close()

    agg: dict[str, dict[str, int]] = {}
    for snaps in grouped.values():
        start, end = snaps[0], snaps[-1]
        start_likes = int(start["likes"] or 0)
        delta = int(end["likes"] or 0) - start_likes
        artist = str(end["artist"] or start["artist"] or "")
        bucket = agg.setdefault(artist, {"track_count": 0, "start": 0, "delta": 0})
        bucket["track_count"] += 1
        bucket["start"] += start_likes
        bucket["delta"] += delta

    result: list[ArtistGrowth] = []
    for artist, b in agg.items():
        if b["start"] < min_initial_likes or b["delta"] <= 0:
            continue
        result.append(
            ArtistGrowth(
                artist=artist,
                track_count=b["track_count"],
                start_likes=b["start"],
                end_likes=b["start"] + b["delta"],
                delta_likes=b["delta"],
                growth_rate=_growth_rate(b["delta"], b["start"]),
            )
        )
    result.sort(key=lambda g: (g.growth_rate, g.delta_likes), reverse=True)
    return result[:limit] if limit is not None else result


def format_trend_report(
    artist_trends: list[ArtistGrowth],
    track_trends: list[TrackGrowth],
    *,
    days: int = 7,
) -> str:
    """Formatiert die Trend-Daten als Text (für Telegram / Konsole).

    Enthält Überschrift, Trending Artists und Top-Tracks mit Prozenten und Like-Zuwachs.
    Sind beide Listen leer: 'Keine Trends im Zeitraum erkannt.'
    """
    if not artist_trends and not track_trends:
        return "Keine Trends im Zeitraum erkannt."

    lines = [f"🎧 Trend-Radar ({days} Tage)", ""]
    if artist_trends:
        lines.append("📈 Trending Artists")
        for i, a in enumerate(artist_trends, 1):
            pct = round(a.growth_rate * 100)
            tracks_word = "Track" if a.track_count == 1 else "Tracks"
            lines.append(
                f"{i}. {a.artist} — {pct:+d}% (+{a.delta_likes} Likes, {a.track_count} {tracks_word})"
            )
        lines.append("")
    if track_trends:
        lines.append("🔥 Top-Tracks")
        for i, t in enumerate(track_trends, 1):
            pct = round(t.growth_rate * 100)
            lines.append(f"{i}. {t.artist} – {t.title}: {pct:+d}% (+{t.delta_likes} Likes)")
        lines.append("")
    return "\n".join(lines).rstrip()
