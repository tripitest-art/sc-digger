"""Akzeptanztests: Tag-Rotation & Exploration-Modus (Issue #56)."""
import random
from pathlib import Path

import pytest
import yaml

from sc_digger import main as m
from sc_digger.main import collect_sources, select_exploration_tags
from sc_digger.models import Config, Track
from sc_digger.output import _fmt_track
from tests.test_modes import mk

ROOT = Path(__file__).resolve().parents[2]
CFG = Config.load(ROOT / "config.yaml")


def test_config_defaults():
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    s = raw["search"]
    assert "exploration_tags" in s
    assert isinstance(s["exploration_tags"], list)
    assert len(s["exploration_tags"]) > 0
    assert "acid techno" in s["exploration_tags"]
    assert s.get("exploration_probability") == 0.0


def test_select_exploration_tags_probability():
    pool = ["acid techno", "warehouse techno"]
    existing = ["schranz", "hardtechno"]

    # Zufallswert 0.1 < 0.2 -> Tag ausgewählt
    rng_hit = random.Random()
    rng_hit.random = lambda: 0.1
    chosen = select_exploration_tags(pool, existing, probability=0.2, rng=rng_hit)
    assert len(chosen) == 1
    assert chosen[0] in pool

    # Zufallswert 0.5 >= 0.2 -> leer
    rng_miss = random.Random()
    rng_miss.random = lambda: 0.5
    assert select_exploration_tags(pool, existing, probability=0.2, rng=rng_miss) == []

    # Probability 0.0 -> immer leer
    assert select_exploration_tags(pool, existing, probability=0.0) == []


def test_select_exploration_tags_skips_existing_tags():
    pool = ["schranz", "acid techno"]
    existing = ["schranz", "hardtechno"]
    rng_hit = random.Random()
    rng_hit.random = lambda: 0.05
    chosen = select_exploration_tags(pool, existing, probability=0.5, rng=rng_hit)
    assert chosen == ["acid techno"]


class FakeSC:
    def __init__(self):
        self.tag_calls = []

    def search_tag(self, tag, age, limit):
        self.tag_calls.append(tag)
        return [mk(100 + len(self.tag_calls), title=f"Track {tag}")]

    def user_uploads(self, url, age):
        return []

    def reference_activity(self, url, age, limit=50):
        return []


def test_collect_sources_marks_exploration_tracks():
    sc = FakeSC()
    s_cfg = {
        "tags": ["schranz"],
        "followed_users": [],
        "reference_accounts": [],
        "exploration_tags": ["acid techno"],
        "exploration_probability": 1.0,  # sicher auslösen
        "max_age_days": 14,
        "limit_per_tag": 10,
    }
    rng = random.Random()
    rng.random = lambda: 0.0

    d = collect_sources(sc, s_cfg, rng=rng)
    assert "schranz" in sc.tag_calls
    assert "acid techno" in sc.tag_calls
    assert len(d.tracks) == 2

    regular_track = [t for t in d.tracks if "schranz" in t.title][0]
    assert regular_track.exploration_tag is None

    exp_track = [t for t in d.tracks if "acid techno" in t.title][0]
    assert exp_track.exploration_tag == "acid techno"


def test_fmt_track_displays_exploration_badge():
    t = mk(1, title="Acid Track")
    t.exploration_tag = "acid techno"
    formatted = _fmt_track(t)
    assert "🔍 via #acid techno" in formatted or "🔍 via #acidtechno" in formatted
