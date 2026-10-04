"""SoundCloud-Trend-Radar (Phase 4): Wachstumsanalyse historischer Engagement-Snapshots.

Liest die Tabelle `track_snapshots` aus der Track-DB (`sc_digger/db.py`, Migration 4) und
ermittelt, welche Tracks und Artists über ein Zeitfenster die stärksten Like-Zuwächse hatten.
Das Zeitfenster ist relativ zum jüngsten vorhandenen Snapshot, nicht zur Wanduhr: Die Analyse
soll auch dann reproduzierbar sein, wenn der letzte Snapshot etwas zurückliegt (z. B. nach
einer Pause des täglichen Laufs).
"""
from __future__ import annotations

import json
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
    delta_plays: int = 0


@dataclass
class ArtistGrowth:
    artist: str
    track_count: int
    start_likes: int
    end_likes: int
    delta_likes: int
    growth_rate: float
    delta_plays: int = 0


@dataclass
class TagTrend:
    tag: str
    track_count: int           # verschiedene Tracks mit diesem Tag im Fenster
    avg_engagement: float      # Mittelwert likes+reposts+comments am Ende des Fensters (je Track)
    delta_engagement: float    # Summe(Ende) - Summe(Start) über diese Tracks
    growth_rate: float         # delta_engagement / Summe(Start)


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


def _columns(con: sqlite3.Connection) -> set[str]:
    """Spaltennamen der Tabelle `track_snapshots` (für minimale Fremd-Tabellen)."""
    try:
        return {str(r[1]) for r in con.execute("PRAGMA table_info(track_snapshots)").fetchall()}
    except sqlite3.Error:
        return set()


def _snapshots(con: sqlite3.Connection, cutoff: str) -> list[sqlite3.Row]:
    """Snapshots im Fenster inkl. Plays/Reposts/Comments/Tags.

    Fehlende Spalten (ältere/minimale Tabellen ohne sie) werden durch `0 AS plays` bzw.
    `NULL AS tags` ersetzt, statt die ganze Abfrage scheitern zu lassen.
    """
    cols = _columns(con)
    plays = "plays" if "plays" in cols else "0 AS plays"
    reposts = "reposts" if "reposts" in cols else "0 AS reposts"
    comments = "comments" if "comments" in cols else "0 AS comments"
    tags = "tags" if "tags" in cols else "NULL AS tags"
    return con.execute(
        f"""
        SELECT sc_id, artist, title, likes, recorded_at,
               {plays}, {reposts}, {comments}, {tags}
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
        delta_plays = int(end["plays"] or 0) - int(start["plays"] or 0)
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
                delta_plays=delta_plays,
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
        delta_plays = int(end["plays"] or 0) - int(start["plays"] or 0)
        artist = str(end["artist"] or start["artist"] or "")
        bucket = agg.setdefault(
            artist, {"track_count": 0, "start": 0, "delta": 0, "delta_plays": 0}
        )
        bucket["track_count"] += 1
        bucket["start"] += start_likes
        bucket["delta"] += delta
        bucket["delta_plays"] += delta_plays

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
                delta_plays=b["delta_plays"],
            )
        )
    result.sort(key=lambda g: (g.growth_rate, g.delta_likes), reverse=True)
    return result[:limit] if limit is not None else result


def _tags_of(row: sqlite3.Row) -> list[str]:
    """Normalisierte Tags (strip, lower) eines Snapshots; leere/ungültige Tags -> []."""
    raw = row["tags"] if "tags" in row.keys() else None
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    seen: list[str] = []
    for tag in parsed:
        if not isinstance(tag, str):
            continue
        norm = tag.strip().lower()
        if norm and norm not in seen:
            seen.append(norm)
    return seen


def calculate_tag_trends(
    track_db_path: str | Path,
    *,
    days: int = 7,
    min_initial_engagement: int = 20,
    min_tracks: int = 2,
    limit: int = 10,
) -> list[TagTrend]:
    """Trends von Tags über das Zeitfenster (letzte `days` Tage).

    Fenster wie calculate_track_growth (relativ zum jüngsten Snapshot). Je Track: ältester
    gegen neuesten Snapshot im Fenster, Engagement = likes + reposts + comments. Die Tags
    eines Tracks stammen aus dem jüngsten Snapshot mit gültigen Tags (JSON-Liste),
    normalisiert mit strip() und lower(); doppelte Tags eines Tracks zählen einmal;
    ungültiges JSON, NULL oder leere Tags -> Track zählt für keinen Tag. Je Tag: nur wenn
    track_count >= min_tracks, Summe(Start) >= min_initial_engagement und delta > 0.
    Sortierung: growth_rate absteigend, delta_engagement absteigend, tag aufsteigend.
    Fehlende DB oder Tabelle -> []; keine Exception.
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

    buckets: dict[str, dict[str, float]] = {}
    for snaps in grouped.values():
        start, end = snaps[0], snaps[-1]
        start_eng = (int(start["likes"] or 0) + int(start["reposts"] or 0)
                     + int(start["comments"] or 0))
        end_eng = (int(end["likes"] or 0) + int(end["reposts"] or 0)
                   + int(end["comments"] or 0))

        tags: list[str] = []
        for snap in reversed(snaps):
            tags = _tags_of(snap)
            if tags:
                break

        for tag in tags:
            b = buckets.setdefault(tag, {"tracks": 0, "start": 0, "delta": 0, "end": 0})
            b["tracks"] += 1
            b["start"] += start_eng
            b["delta"] += end_eng - start_eng
            b["end"] += end_eng

    result: list[TagTrend] = []
    for tag, b in buckets.items():
        if b["tracks"] < min_tracks or b["start"] < min_initial_engagement or b["delta"] <= 0:
            continue
        result.append(
            TagTrend(
                tag=tag,
                track_count=int(b["tracks"]),
                avg_engagement=b["end"] / b["tracks"],
                delta_engagement=b["delta"],
                growth_rate=_growth_rate(int(b["delta"]), int(b["start"])),
            )
        )
    result.sort(key=lambda t: (-t.growth_rate, -t.delta_engagement, t.tag))
    return result[:limit] if limit is not None else result


def format_trend_report(
    artist_trends: list[ArtistGrowth],
    track_trends: list[TrackGrowth],
    *,
    days: int = 7,
    tag_trends: list[TagTrend] | None = None,
) -> str:
    """Formatiert die Trend-Daten als Text (für Telegram / Konsole).

    Enthält Überschrift, Trending Artists, Trending Tags und Top-Tracks mit Prozenten,
    Like-Zuwachs und – sofern vorhanden – Plays-Zuwachs. Sind alle Listen leer:
    'Keine Trends im Zeitraum erkannt.'
    """
    if not artist_trends and not track_trends and not tag_trends:
        return "Keine Trends im Zeitraum erkannt."

    lines = [f"🎧 Trend-Radar ({days} Tage)", ""]
    if artist_trends:
        lines.append("📈 Trending Artists")
        for i, a in enumerate(artist_trends, 1):
            pct = round(a.growth_rate * 100)
            tracks_word = "Track" if a.track_count == 1 else "Tracks"
            plays = f", +{a.delta_plays} Plays" if a.delta_plays > 0 else ""
            lines.append(
                f"{i}. {a.artist} — {pct:+d}% (+{a.delta_likes} Likes{plays}, {a.track_count} {tracks_word})"
            )
        lines.append("")
    if tag_trends:
        lines.append("🏷️ Trending Tags")
        for i, t in enumerate(tag_trends, 1):
            pct = round(t.growth_rate * 100)
            tracks_word = "Track" if t.track_count == 1 else "Tracks"
            lines.append(
                f"{i}. {t.tag} — {pct:+d}% (⌀ {round(t.avg_engagement)} Engagement, "
                f"{t.track_count} {tracks_word})"
            )
        lines.append("")
    if track_trends:
        lines.append("🔥 Top-Tracks")
        for i, t in enumerate(track_trends, 1):
            pct = round(t.growth_rate * 100)
            plays = f", +{t.delta_plays} Plays" if t.delta_plays > 0 else ""
            lines.append(f"{i}. {t.artist} – {t.title}: {pct:+d}% (+{t.delta_likes} Likes{plays})")
        lines.append("")
    return "\n".join(lines).rstrip()
