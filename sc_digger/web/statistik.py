"""Statistik- und Trend-Radar-Aggregation für die Web-Oberfläche (Issue #658)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..stats import calculate_stats
from ..trends import (
    calculate_artist_trends,
    calculate_tag_trends,
    calculate_track_growth,
)

MIN_DAYS = 1
MAX_DAYS = 365


def collect_statistics(
    track_db_path: str | Path,
    state_db_path: str | Path,
    *,
    days: int = 7,
) -> dict[str, Any]:
    """{"days": days,
        "funnel": {"runs_total", "runs_ok", "tracks_scanned", "tracks_inbox", "tracks_rejected",
                   "likes", "dislikes", "later", "top_artists"},
        "trends": {"artists": [{"name", "growth_rate", "delta"}],
                   "tracks": [{"artist", "title", "growth_rate", "delta_likes"}],
                   "tags":   [{"tag", "growth_rate", "delta"}]}}

    ValueError bei days < MIN_DAYS oder > MAX_DAYS.
    Fehlende DB-Dateien: leere Werte, kein Fehler (calculate_stats nur aufrufen, wenn BEIDE
    Dateien existieren — sonst sqlite3.connect legt sie an).
    Trends kommen aus calculate_artist_trends / calculate_track_growth / calculate_tag_trends
    (sc_digger/trends.py), jeweils days=days, Standardgrenzen; growth_rate auf 3 Nachkommastellen
    gerundet.
    """
    if not isinstance(days, int) or isinstance(days, bool) or days < MIN_DAYS or days > MAX_DAYS:
        raise ValueError(
            f"Zeitfenster muss zwischen {MIN_DAYS} und {MAX_DAYS} Tagen liegen."
        )

    track_p = Path(track_db_path)
    state_p = Path(state_db_path)

    if track_p.is_file() and state_p.is_file():
        stats = calculate_stats(track_p, state_p, days=days)
        funnel = {
            "runs_total": stats.runs_total,
            "runs_ok": stats.runs_ok,
            "tracks_scanned": stats.tracks_scanned,
            "tracks_inbox": stats.tracks_inbox,
            "tracks_rejected": stats.tracks_rejected,
            "likes": stats.likes,
            "dislikes": stats.dislikes,
            "later": stats.later,
            "top_artists": stats.top_artists,
        }
    else:
        funnel = {
            "runs_total": 0,
            "runs_ok": 0,
            "tracks_scanned": 0,
            "tracks_inbox": 0,
            "tracks_rejected": 0,
            "likes": 0,
            "dislikes": 0,
            "later": 0,
            "top_artists": [],
        }

    if track_p.is_file():
        artist_trends = calculate_artist_trends(track_p, days=days)
        track_growth = calculate_track_growth(track_p, days=days)
        tag_trends = calculate_tag_trends(track_p, days=days)

        artists = [
            {
                "name": a.artist,
                "growth_rate": round(a.growth_rate, 3),
                "delta": a.delta_likes,
            }
            for a in artist_trends
        ]
        tracks = [
            {
                "artist": t.artist,
                "title": t.title,
                "growth_rate": round(t.growth_rate, 3),
                "delta_likes": t.delta_likes,
            }
            for t in track_growth
        ]
        tags = [
            {
                "tag": tg.tag,
                "growth_rate": round(tg.growth_rate, 3),
                "delta": int(tg.delta_engagement),
            }
            for tg in tag_trends
        ]
    else:
        artists = []
        tracks = []
        tags = []

    return {
        "days": days,
        "funnel": funnel,
        "trends": {
            "artists": artists,
            "tracks": tracks,
            "tags": tags,
        },
    }
