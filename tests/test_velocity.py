"""Unit-Tests für Engagement-Velocity und zeitbasiertes Scoring (Issue #106)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sc_digger.models import Config, Track
from sc_digger.pipeline import engagement_velocity, score_tracks
from tests.test_modes import mk

ROOT = Path(__file__).resolve().parents[1]
CFG = Config.load(ROOT / "config.yaml")
NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)


def test_track_model_velocity_default_and_override():
    """Track-Dataclass hat default velocity=0.0 und erlaubt Vorgabe."""
    t_def = mk(1)
    assert t_def.velocity == 0.0

    t_direct = Track(
        id=2, title="T", url="u", artist="A", artist_url="",
        created_at=NOW.isoformat(), duration_ms=1000, genre="", tags=[],
        description="", bpm=None, plays=10, likes=5, reposts=1, comments=0,
        downloadable=False, has_downloads_left=False, purchase_url=None, purchase_title=None,
        velocity=3.5,
    )
    assert t_direct.velocity == 3.5


def test_engagement_velocity_none_and_edge_inputs():
    """Sonderfälle: None-Objekt, None/negative Likes, fehlerhafte Datumsangaben."""
    assert engagement_velocity(None, now=NOW) == 0.0

    t_none_likes = mk(1, created_at=NOW.isoformat(), likes_count=None)
    assert engagement_velocity(t_none_likes, now=NOW) == 0.0

    t_neg_likes = mk(2, created_at=NOW.isoformat(), likes_count=-10)
    assert engagement_velocity(t_neg_likes, now=NOW) == 0.0

    t_ws = mk(3, created_at="   ", likes_count=20)
    assert engagement_velocity(t_ws, now=NOW) == 0.0

    for bad_date in ["2026-99-99", "ungueltiges-datum", "abcTdefZ"]:
        t_bad = mk(4, created_at=bad_date, likes_count=20)
        assert engagement_velocity(t_bad, now=NOW) == 0.0


def test_engagement_velocity_now_default():
    """Ohne now-Parameter wird die aktuelle UTC-Zeit verwendet."""
    created = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    t = mk(1, created_at=created, likes_count=20)
    vel = engagement_velocity(t)
    # ca. 10 Likes/Stunde (mit geringer Toleranz für Testlauf-Dauer)
    assert 9.5 <= vel <= 10.5


def test_engagement_velocity_naive_now_and_custom_timezones():
    """Naive Zeitstempel für now sowie Zeitzonen-Offsets werden korrekt als UTC behandelt."""
    # now als naive datetime
    now_naive = datetime(2026, 9, 30, 12, 0, 0)
    created = datetime(2026, 9, 30, 10, 0, 0, tzinfo=timezone.utc).isoformat()
    t = mk(1, created_at=created, likes_count=20)
    assert engagement_velocity(t, now=now_naive) == 10.0

    # now in UTC+3 (15:00 UTC+3 entspricht 12:00 UTC)
    tz_plus3 = timezone(timedelta(hours=3))
    now_plus3 = datetime(2026, 9, 30, 15, 0, 0, tzinfo=tz_plus3)
    assert engagement_velocity(t, now=now_plus3) == 10.0


def test_engagement_velocity_custom_min_age_hours():
    """min_age_hours kann als Keyword-Argument angepasst werden."""
    # 30 Minuten alt (0.5 h), 10 Likes
    created = (NOW - timedelta(minutes=30)).isoformat()
    t = mk(1, created_at=created, likes_count=10)

    # Mit min_age_hours=2.0 gedämpft -> 10 / 2.0 = 5.0
    assert engagement_velocity(t, now=NOW, min_age_hours=2.0) == 5.0

    # Mit min_age_hours=0.5 -> 10 / 0.5 = 20.0
    assert engagement_velocity(t, now=NOW, min_age_hours=0.5) == 20.0


def test_engagement_velocity_future_upload_skew():
    """Uploads in der Zukunft (Clock-Skew) werden auf min_age_hours gedämpft."""
    created_future = (NOW + timedelta(hours=2)).isoformat()
    t = mk(1, created_at=created_future, likes_count=15)
    # Gedämpft auf min_age_hours=1.0 -> 15 / 1.0 = 15.0
    assert engagement_velocity(t, now=NOW, min_age_hours=1.0) == 15.0


def test_score_tracks_populates_velocity_on_pool():
    """score_tracks setzt t.velocity auf allen Tracks im bewerteten Pool."""
    tracks = [
        mk(1, playback_count=500, likes_count=50,
           created_at=(datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()),
        mk(2, playback_count=500, likes_count=0,
           created_at=(datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()),
    ]
    scored = score_tracks(tracks, CFG, apply_filter=False)
    by_id = {t.id: t for t in scored}

    assert hasattr(by_id[1], "velocity")
    assert by_id[1].velocity > 0.0
    assert by_id[2].velocity == 0.0


def test_score_tracks_handles_empty_pool_and_min_plays():
    """score_tracks wirft nicht bei leerer Liste und beachtet apply_filter."""
    assert score_tracks([], CFG) == []

    # Track unter min_plays (300 in CFG)
    low_plays = mk(1, playback_count=100, likes_count=50)
    normal_plays = mk(2, playback_count=500, likes_count=50)

    # Mit Filter wird low_plays aussortiert
    filtered = score_tracks([low_plays, normal_plays], CFG, apply_filter=True)
    assert len(filtered) == 1
    assert filtered[0].id == 2

    # Ohne Filter bleibt low_plays enthalten und erhält velocity
    unfiltered = score_tracks([low_plays, normal_plays], CFG, apply_filter=False)
    assert len(unfiltered) == 2
    assert all(hasattr(t, "velocity") for t in unfiltered)


def test_score_tracks_zero_velocity_weight_equivalence():
    """Ein Gewicht velocity=0.0 liefert exakt dieselben Scores wie eine Konfiguration ohne velocity."""
    cfg_no_key = Config({
        "scoring": {
            "min_plays": 100,
            "min_like_ratio": 0.01,
            "min_percentile": 0,
            "weights": {
                "like_ratio": 0.45,
                "repost_ratio": 0.35,
                "comment_ratio": 0.10,
                "recency": 0.10,
            },
        },
        "search": {"max_age_days": 14},
    })
    cfg_zero_key = Config({
        "scoring": {
            "min_plays": 100,
            "min_like_ratio": 0.01,
            "min_percentile": 0,
            "weights": {
                "like_ratio": 0.45,
                "repost_ratio": 0.35,
                "comment_ratio": 0.10,
                "recency": 0.10,
                "velocity": 0.0,
            },
        },
        "search": {"max_age_days": 14},
    })

    t1 = mk(1, playback_count=1000, likes_count=80, created_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    t2 = mk(2, playback_count=1000, likes_count=30, created_at=(datetime.now(timezone.utc) - timedelta(days=5)).isoformat())

    scored_no_key = score_tracks([t1, t2], cfg_no_key, apply_filter=False)
    scored_zero_key = score_tracks([t1, t2], cfg_zero_key, apply_filter=False)

    score_map_no = {t.id: t.score for t in scored_no_key}
    score_map_zero = {t.id: t.score for t in scored_zero_key}

    assert score_map_no == score_map_zero
