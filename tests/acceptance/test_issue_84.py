"""Akzeptanztests: kurze Mixe/Podcasts per Titelmuster in discover aussortieren."""
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from sc_digger.models import Config
from sc_digger.pipeline import filter_sets, is_mix_title, mark_sets
from sc_digger.soundcloud import SoundCloudClient

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime.now(timezone.utc).isoformat()
PATTERNS = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["search"]["set_title_patterns"]


def mk(i, title, minutes=6.0):
    return SoundCloudClient._to_track(dict(
        id=i, kind="track", title=title, permalink_url=f"https://soundcloud.com/a/t{i}",
        user={"username": f"Artist{i}", "permalink_url": "https://soundcloud.com/a"},
        created_at=NOW, playback_count=1000, likes_count=50, reposts_count=5, comment_count=1,
        downloadable=False, tag_list="", description="", duration=int(minutes * 60_000)))


@pytest.mark.parametrize("title", [
    "JAULIN SELECTS 001 — Missing (AZYR Live Hard Techno Blend)",
    "Schranz Podcast 042",
    "Hard Techno Mix 007",
    "Mixtape Vol. 3",
    "DJ Set @ Bunker (excerpt)",
    "Live-Set Tresor",
    "Radio Show #12",
])
def test_mix_titles_are_detected(title):
    assert is_mix_title(mk(1, title), PATTERNS) is True


@pytest.mark.parametrize("title", [
    "Some Track (Original Mix)",
    "Banger (Extended Mix)",
    "Tune (Schranz Remix)",
    "NØSS – Join me (165bpm schranz mix)",
    "Setback",
    "Blender",
    "Podcasting Nights (Original Mix)",
])
def test_normal_titles_are_not_detected(title):
    assert is_mix_title(mk(1, title), PATTERNS) is False


def test_is_mix_title_without_patterns_is_false():
    assert is_mix_title(mk(1, "Schranz Podcast 042"), []) is False


def test_filter_sets_drops_mix_titles_and_logs_separately(caplog):
    cfg = Config({"search": {"max_duration_min": 12, "set_title_patterns": PATTERNS}})
    tracks = [mk(1, "Tune A"), mk(2, "JAULIN SELECTS 001 (Blend)"), mk(3, "Long One", 60),
              mk(4, "Tune B (Original Mix)")]
    with caplog.at_level(logging.INFO, logger="sc_digger.pipeline"):
        kept = filter_sets(tracks, cfg)
    assert [t.id for t in kept] == [1, 4]
    assert "DJ-Sets aussortiert: 1" in caplog.text
    assert "Mix-Titel aussortiert: 1" in caplog.text


def test_filter_sets_without_patterns_unchanged():
    cfg = Config({"search": {"max_duration_min": 12}})
    assert [t.id for t in filter_sets([mk(1, "Schranz Podcast 042")], cfg)] == [1]


def test_mark_sets_ignores_titles():
    cfg = Config({"search": {"max_duration_min": 12, "set_title_patterns": PATTERNS}})
    t = mk(1, "Schranz Podcast 042")
    mark_sets([t], cfg)
    assert t.set_minutes is None


def test_config_patterns_are_valid_regex():
    import re
    assert PATTERNS and all(re.compile(p, re.IGNORECASE) for p in PATTERNS)
