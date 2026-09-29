"""Unit-Tests für Sperrliste von Promo-Accounts (Issue #95)."""
import logging
from pathlib import Path

import pytest

from sc_digger.models import Config, Track
from sc_digger.pipeline import account_slug, is_blocked, score_tracks
from tests.test_modes import mk

ROOT = Path(__file__).resolve().parents[1]
CFG = Config.load(ROOT / "config.yaml")


def by(i, slug, reference_hit: bool = False, **kw):
    t = mk(i, user={"username": slug, "permalink_url": f"https://soundcloud.com/{slug}"}, **kw)
    t.reference_hit = reference_hit
    return t


def _cfg(blocked):
    cfg = Config(dict(CFG.raw))
    cfg.raw["scoring"] = {**CFG["scoring"], "blocked_accounts": list(blocked)}
    return cfg


class TestAccountSlug:
    def test_handles_fragments_and_deep_paths(self):
        assert account_slug("https://soundcloud.com/artist-name#details") == "artist-name"
        assert account_slug("https://soundcloud.com/label/artist-name") == "artist-name"
        assert account_slug("artist-slug/") == "artist-slug"

    def test_handles_non_string_and_invalid_inputs(self):
        assert account_slug(12345) == "12345"
        assert account_slug("   ") == ""
        assert account_slug(None) == ""
        assert account_slug("https://soundcloud.com/") == ""


class TestIsBlocked:
    def test_reference_hit_is_still_blocked(self):
        t = by(1, "blocked-promo", reference_hit=True)
        assert is_blocked(t, ["blocked-promo"]) is True
        assert is_blocked(t, ["https://soundcloud.com/blocked-promo"]) is True

    def test_multiple_blocked_accounts(self):
        blocked = ["promo-one", "https://soundcloud.com/promo-two", "promo-three"]
        t1 = by(1, "promo-one")
        t2 = by(2, "promo-two")
        t3 = by(3, "promo-three")
        t4 = by(4, "allowed-artist")
        assert is_blocked(t1, blocked) is True
        assert is_blocked(t2, blocked) is True
        assert is_blocked(t3, blocked) is True
        assert is_blocked(t4, blocked) is False

    def test_none_and_empty_entries_in_blocked_list(self):
        t = by(1, "promo-net")
        assert is_blocked(t, [None, "", "  ", "other-account"]) is False
        assert is_blocked(t, [None, "promo-net", ""]) is True

    def test_edge_cases_track_or_blocked_empty(self):
        assert is_blocked(None, ["promo"]) is False
        assert is_blocked(by(1, "promo"), []) is False
        assert is_blocked(by(1, "promo"), None) is False

    def test_track_without_artist_url_or_empty_artist_url(self):
        t = mk(1, user={"username": "promo", "permalink_url": None})
        assert is_blocked(t, ["promo"]) is False
        t_empty = mk(2, user={"username": "promo", "permalink_url": "   "})
        assert is_blocked(t_empty, ["promo"]) is False


class TestScoreTracksBlockedAccounts:
    def test_reference_hit_of_blocked_account_is_dropped(self, caplog):
        base = [by(i, f"artist{i}") for i in range(1, 15)]
        blocked_ref = by(99, "promo-net", reference_hit=True, likes_count=500)
        tracks = base + [blocked_ref]

        with caplog.at_level(logging.INFO, logger="sc_digger.pipeline"):
            kept = score_tracks(tracks, _cfg(["promo-net"]), apply_filter=True)

        assert 99 not in {t.id for t in kept}
        assert "Gesperrte Accounts aussortiert: 1" in caplog.text

    def test_multiple_blocked_accounts_dropped(self, caplog):
        base = [by(i, f"artist{i}") for i in range(1, 15)]
        b1 = by(101, "spammer1")
        b2 = by(102, "spammer2")
        tracks = base + [b1, b2]

        with caplog.at_level(logging.INFO, logger="sc_digger.pipeline"):
            kept = score_tracks(tracks, _cfg(["spammer1", "https://soundcloud.com/spammer2"]), apply_filter=True)

        assert 101 not in {t.id for t in kept}
        assert 102 not in {t.id for t in kept}
        assert "Gesperrte Accounts aussortiert: 2" in caplog.text

    def test_none_entries_in_config_blocked_accounts(self):
        base = [by(i, f"artist{i}") for i in range(1, 15)]
        b1 = by(99, "spammer")
        tracks = base + [b1]

        kept = score_tracks(tracks, _cfg([None, "", "spammer"]), apply_filter=True)
        assert 99 not in {t.id for t in kept}

    def test_apply_filter_false_keeps_reference_hit_of_blocked_account(self):
        tracks = [by(1, "artist1"), by(2, "promo-net", reference_hit=True)]
        kept = score_tracks(tracks, _cfg(["promo-net"]), apply_filter=False)
        assert {t.id for t in kept} == {1, 2}

    def test_non_blocked_reference_hit_is_boosted(self):
        tracks = [
            by(1, "legit-artist", reference_hit=True, likes_count=100),
            by(2, "promo-net", likes_count=500),
        ] + [by(i, f"other{i}", likes_count=50) for i in range(3, 20)]
        kept = score_tracks(tracks, _cfg(["promo-net"]), apply_filter=True)
        assert 2 not in {t.id for t in kept}
        legit = next(t for t in kept if t.id == 1)
        assert legit.reference_hit is True
