"""Eigene Tests für DJ-Set-Filter / Mix-Titel-Erkennung (Issue #84)."""
import logging
from datetime import datetime, timezone

import pytest

from sc_digger.models import Config
from sc_digger.pipeline import filter_sets, is_mix_title
from sc_digger.soundcloud import SoundCloudClient

NOW = datetime.now(timezone.utc).isoformat()


def _mk_track(i: int, title: str | None, minutes: float = 6.0):
    return SoundCloudClient._to_track(
        dict(
            id=i,
            kind="track",
            title=title,
            permalink_url=f"https://soundcloud.com/artist/t{i}",
            user={"username": f"Artist{i}", "permalink_url": "https://soundcloud.com/artist"},
            created_at=NOW,
            playback_count=500,
            likes_count=20,
            reposts_count=2,
            comment_count=0,
            downloadable=False,
            tag_list="",
            description="",
            duration=int(minutes * 60_000),
        )
    )


def test_invalid_pattern_logs_warning_and_continues(caplog):
    """Ungültiges Regex-Muster führt zu log.warning, kein Absturz, übrige Muster wirken weiter."""
    patterns = ["[invalid(regex", r"\bpodcast\b"]
    track = _mk_track(1, "Techno Podcast 01")
    with caplog.at_level(logging.WARNING, logger="sc_digger.pipeline"):
        matched = is_mix_title(track, patterns)
    assert matched is True
    assert "Ungültiges Regex-Muster" in caplog.text
    assert "[invalid(regex" in caplog.text


def test_title_none_does_not_crash():
    """Titel None wird wie leere Zeichenkette behandelt und stürzt nicht ab."""
    patterns = [r"\bpodcast\b", r"\bmix\b"]
    track = _mk_track(1, "valid title")
    track.title = None
    assert is_mix_title(track, patterns) is False
    assert is_mix_title(None, patterns) is False



def test_track_matching_duration_and_title_counts_only_as_dj_set(caplog):
    """Track, der per Dauer UND per Titel passt, zählt nur im 'DJ-Sets'-Log."""
    patterns = [r"\bpodcast\b", r"\bselects\b"]
    cfg = Config({"search": {"max_duration_min": 12, "set_title_patterns": patterns}})
    tracks = [
        _mk_track(1, "Techno Podcast 01", minutes=60.0),  # Passt auf Dauer UND Titel
        _mk_track(2, "Short Track", minutes=5.0),
    ]
    with caplog.at_level(logging.INFO, logger="sc_digger.pipeline"):
        kept = filter_sets(tracks, cfg)
    assert [t.id for t in kept] == [2]
    assert "DJ-Sets aussortiert: 1" in caplog.text
    assert "Mix-Titel aussortiert" not in caplog.text


def test_order_preserved_when_mix_titles_dropped():
    """Reihenfolge der verbliebenen Tracks bleibt stabil erhalten."""
    patterns = [r"\bselects\b"]
    cfg = Config({"search": {"max_duration_min": 12, "set_title_patterns": patterns}})
    tracks = [
        _mk_track(10, "First Track"),
        _mk_track(20, "Selects 01"),
        _mk_track(30, "Second Track"),
        _mk_track(40, "Selects 02"),
        _mk_track(50, "Third Track"),
    ]
    kept = filter_sets(tracks, cfg)
    assert [t.id for t in kept] == [10, 30, 50]


def test_no_mix_titles_removed_does_not_log_mix_titles(caplog):
    """Wenn keine Mix-Titel aussortiert werden, gibt es keinen Mix-Titel-Logeintrag."""
    patterns = [r"\bselects\b"]
    cfg = Config({"search": {"max_duration_min": 12, "set_title_patterns": patterns}})
    tracks = [_mk_track(1, "Track A"), _mk_track(2, "Track B")]
    with caplog.at_level(logging.INFO, logger="sc_digger.pipeline"):
        kept = filter_sets(tracks, cfg)
    assert len(kept) == 2
    assert "Mix-Titel aussortiert" not in caplog.text
    assert "DJ-Sets aussortiert" not in caplog.text


def test_case_insensitive_matching():
    """Groß-/Kleinschreibung wird ignoriert."""
    patterns = [r"\bselects\b", r"\bpodcast\b"]
    assert is_mix_title(_mk_track(1, "SELECTS 001"), patterns) is True
    assert is_mix_title(_mk_track(2, "PoDcAsT #5"), patterns) is True
    assert is_mix_title(_mk_track(3, "Regular Track"), patterns) is False
