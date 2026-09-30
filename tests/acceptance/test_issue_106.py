"""Akzeptanztests: Zeitbasiertes Engagement-Scoring (Engagement Velocity)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from sc_digger.models import Config, Track
from sc_digger.pipeline import engagement_velocity, score_tracks
from tests.test_modes import mk

ROOT = Path(__file__).resolve().parents[2]
CFG = Config.load(ROOT / "config.yaml")
NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)


def test_config_defaults():
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    weights = raw["scoring"]["weights"]
    assert "velocity" in weights
    assert weights["velocity"] == 0.20
    total = sum(weights.values())
    assert abs(total - 1.0) < 1e-6


def test_engagement_velocity_standard_hours():
    # Track 24 Stunden alt, 48 Likes -> 2.0 Likes/Stunde
    created = (NOW - timedelta(hours=24)).isoformat()
    t = mk(1, created_at=created, likes_count=48)
    assert engagement_velocity(t, now=NOW) == 2.0

    # Track 48 Stunden alt, 48 Likes -> 1.0 Likes/Stunde
    created_old = (NOW - timedelta(hours=48)).isoformat()
    t_old = mk(2, created_at=created_old, likes_count=48)
    assert engagement_velocity(t_old, now=NOW) == 1.0


def test_engagement_velocity_min_age_clamping():
    # Track 15 Minuten alt (0.25 h), 10 Likes -> gedämpft auf min_age_hours=1.0 -> 10.0 Likes/h
    created_fresh = (NOW - timedelta(minutes=15)).isoformat()
    t = mk(1, created_at=created_fresh, likes_count=10)
    assert engagement_velocity(t, now=NOW) == 10.0

    # Track aus der Zukunft (Clock-Skew) -> gedämpft auf min_age_hours=1.0 -> 5.0 Likes/h
    created_future = (NOW + timedelta(minutes=10)).isoformat()
    t_future = mk(2, created_at=created_future, likes_count=5)
    assert engagement_velocity(t_future, now=NOW) == 5.0


def test_engagement_velocity_invalid_or_missing_created_at():
    # Ungültige oder fehlende Datumsangaben dürfen nicht werfen und liefern 0.0
    for bad in ["", "kein-datum", None]:
        t = mk(1, created_at=bad, likes_count=100)
        assert engagement_velocity(t, now=NOW) == 0.0

    # 0 oder negative Likes liefern 0.0
    t_zero = mk(2, created_at=NOW.isoformat(), likes_count=0)
    assert engagement_velocity(t_zero, now=NOW) == 0.0


def test_engagement_velocity_respects_timezones():
    # UTC mit 'Z'
    t_z = mk(1, created_at="2026-09-30T10:00:00Z", likes_count=20)
    # 2 Stunden Differenz zu NOW (12:00 UTC) -> 10.0 Likes/h
    assert engagement_velocity(t_z, now=NOW) == 10.0

    # UTC mit Offset '+02:00' (12:00+02:00 entspricht 10:00 UTC) -> 2 Stunden Differenz
    t_offset = mk(2, created_at="2026-09-30T12:00:00+02:00", likes_count=20)
    assert engagement_velocity(t_offset, now=NOW) == 10.0


def test_score_tracks_rewards_fresh_high_velocity_track():
    # 20 Standard-Tracks als Baseline-Pool
    tracks = [
        mk(i, playback_count=1000, likes_count=40,
           created_at=(NOW - timedelta(days=5)).isoformat())
        for i in range(1, 21)
    ]
    # Track A: 1 Tag alt, 100 Likes -> 100/24 = 4.17 Likes/h (hohes Tempo)
    track_a = mk(101, playback_count=1000, likes_count=100,
                 created_at=(NOW - timedelta(days=1)).isoformat())
    # Track B: 10 Tage alt, 100 Likes -> 100/240 = 0.42 Likes/h (langsames Tempo)
    track_b = mk(102, playback_count=1000, likes_count=100,
                 created_at=(NOW - timedelta(days=10)).isoformat())

    scored = score_tracks(tracks + [track_a, track_b], CFG, apply_filter=False)
    by_id = {t.id: t for t in scored}

    assert hasattr(by_id[101], "velocity")
    assert by_id[101].velocity > by_id[102].velocity
    assert by_id[101].score > by_id[102].score


def test_score_tracks_without_velocity_weight_is_unaffected():
    # Config ohne velocity-Gewicht verhält sich wie bisher
    cfg_raw = dict(CFG.raw)
    cfg_raw["scoring"] = dict(CFG["scoring"])
    cfg_raw["scoring"]["weights"] = {
        "like_ratio": 0.45,
        "repost_ratio": 0.35,
        "comment_ratio": 0.10,
        "recency": 0.10,
    }
    cfg_no_vel = Config(cfg_raw)

    t1 = mk(1, playback_count=1000, likes_count=50,
            created_at=(NOW - timedelta(days=2)).isoformat())
    t2 = mk(2, playback_count=1000, likes_count=50,
            created_at=(NOW - timedelta(days=2)).isoformat())

    scored = score_tracks([t1, t2], cfg_no_vel, apply_filter=False)
    assert len(scored) == 2
