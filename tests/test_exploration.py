"""Tests für Explorations-Tags in Discovery und Digest (Issue #109)."""
import random

from sc_digger.main import collect_sources, select_exploration_tags
from sc_digger.models import Track
from sc_digger.output import _fmt_track
from sc_digger.soundcloud import ClientIdError, RateLimitError
from tests.test_modes import mk

BASE = {
    "tags": ["schranz"],
    "followed_users": [],
    "reference_accounts": [],
    "exploration_tags": ["acid techno"],
    "exploration_probability": 1.0,
    "max_age_days": 14,
    "limit_per_tag": 10,
}


class FailingRandom:
    """rng, der jeden Zugriff als Fehler markiert – belegt, dass er nicht benutzt wird."""

    def random(self):
        raise AssertionError("rng darf bei probability<=0 nicht benutzt werden")

    def choice(self, seq):
        raise AssertionError("rng darf bei probability<=0 nicht benutzt werden")


def _rng_hit() -> random.Random:
    r = random.Random()
    r.random = lambda: 0.0
    return r


def test_select_no_rng_used_when_disabled():
    assert select_exploration_tags(["acid techno"], ["schranz"], probability=0.0,
                                   rng=FailingRandom()) == []


def test_select_empty_candidates():
    assert select_exploration_tags(["schranz"], ["schranz"], probability=1.0) == []


def test_select_default_rng_picks_candidate():
    chosen = select_exploration_tags(["acid techno", "warehouse techno"], ["schranz"],
                                     probability=1.0)
    assert chosen in (["acid techno"], ["warehouse techno"])


def test_track_default_exploration_tag_is_none():
    assert Track.__dataclass_fields__["exploration_tag"].default is None
    assert mk(1).exploration_tag is None


class RecordingSC:
    """Fake-Client, der die Aufrufe in Reihenfolge protokolliert und optional wirft."""

    def __init__(self, fail_tag=None, exc=None):
        self.calls: list[tuple[str, str]] = []
        self.fail_tag = fail_tag
        self.exc = exc

    def search_tag(self, tag, age, limit):
        self.calls.append(("search_tag", tag))
        if tag == self.fail_tag:
            raise self.exc
        return [mk(len(self.calls) + 1000, title=f"Track {tag}")]

    def user_uploads(self, url, age):
        self.calls.append(("user_uploads", url))
        return []

    def reference_activity(self, url, age, limit=50):
        self.calls.append(("reference_activity", url))
        return []


def test_collect_sources_order_tags_then_exploration_then_followed():
    sc = RecordingSC()
    cfg = {**BASE, "followed_users": ["https://soundcloud.com/label"]}
    d = collect_sources(sc, cfg, rng=_rng_hit())
    assert [c[0] for c in sc.calls] == ["search_tag", "search_tag", "user_uploads"]
    assert [c[1] for c in sc.calls if c[0] == "search_tag"] == ["schranz", "acid techno"]
    assert d.total_sources == 3


def test_collect_sources_no_exploration_when_probability_zero():
    sc = RecordingSC()
    collect_sources(sc, {**BASE, "exploration_probability": 0.0}, rng=_rng_hit())
    assert "acid techno" not in [c[1] for c in sc.calls if c[0] == "search_tag"]


def test_collect_sources_exploration_error_counted_and_failed():
    sc = RecordingSC(fail_tag="acid techno", exc=RuntimeError("kaputt"))
    d = collect_sources(sc, BASE, rng=_rng_hit())
    assert d.total_sources == 2
    assert d.failed and "acid techno" in d.failed[0]
    assert all(t.exploration_tag is None for t in d.tracks)


def test_collect_sources_marks_only_exploration_tracks():
    sc = RecordingSC()
    d = collect_sources(sc, BASE, rng=_rng_hit())
    regular = [t for t in d.tracks if "Track schranz" == t.title]
    exploratory = [t for t in d.tracks if "Track acid techno" == t.title]
    assert regular and regular[0].exploration_tag is None
    assert exploratory and exploratory[0].exploration_tag == "acid techno"


def test_collect_sources_abort_on_tag_skips_exploration():
    sc = RecordingSC(fail_tag="schranz", exc=ClientIdError("token"))
    d = collect_sources(sc, BASE, rng=_rng_hit())
    assert d.aborted == "ClientIdError"
    assert "acid techno" not in [c[1] for c in sc.calls if c[0] == "search_tag"]


def test_collect_sources_abort_on_exploration_skips_followed():
    sc = RecordingSC(fail_tag="acid techno", exc=RateLimitError("limit"))
    cfg = {**BASE, "followed_users": ["https://soundcloud.com/label"]}
    d = collect_sources(sc, cfg, rng=_rng_hit())
    assert d.aborted == "RateLimitError"
    assert not any(c[0] == "user_uploads" for c in sc.calls)


def test_fmt_track_badge_before_quality_lines():
    t = mk(1, title="Acid Track")
    t.exploration_tag = "acid techno"
    t.quality_report = {"ok": True, "ext": "wav", "bitrate_kbps": 1411, "reason": "lossless"}
    out = _fmt_track(t)
    assert out.index("🔍 via #acid techno") < out.index("WAV")


def test_fmt_track_no_badge_without_exploration_tag():
    assert "🔍" not in _fmt_track(mk(1))
