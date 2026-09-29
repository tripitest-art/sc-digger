"""Akzeptanztests: Sperrliste für Promo-Accounts in discover."""
import logging
from pathlib import Path

import pytest
import yaml

from sc_digger.models import Config
from sc_digger.pipeline import account_slug, is_blocked, score_tracks
from tests.test_modes import mk

ROOT = Path(__file__).resolve().parents[2]
CFG = Config.load(ROOT / "config.yaml")


def by(i, slug, **kw):
    return mk(i, user={"username": slug, "permalink_url": f"https://soundcloud.com/{slug}"}, **kw)


def _cfg(blocked):
    cfg = Config(dict(CFG.raw))
    cfg.raw["scoring"] = {**CFG["scoring"], "blocked_accounts": list(blocked)}
    return cfg


def test_config_default_is_empty_list():
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert raw["scoring"]["blocked_accounts"] == []


@pytest.mark.parametrize("value, expected", [
    ("https://soundcloud.com/Promo-Net", "promo-net"),
    ("https://soundcloud.com/promo-net/", "promo-net"),
    ("https://m.soundcloud.com/promo-net?utm_source=x", "promo-net"),
    (" promo-net ", "promo-net"),
    ("", ""),
    (None, ""),
])
def test_account_slug(value, expected):
    assert account_slug(value) == expected


def test_is_blocked_matches_url_or_slug():
    t = by(1, "promo-net")
    assert is_blocked(t, ["https://soundcloud.com/promo-net"]) is True
    assert is_blocked(t, ["Promo-Net"]) is True
    assert is_blocked(t, ["other"]) is False
    assert is_blocked(t, []) is False
    assert is_blocked(mk(2, user={"username": "x", "permalink_url": ""}), ["x"]) is False


def test_score_tracks_drops_blocked_accounts_and_logs(caplog):
    tracks = [by(i, f"artist{i}") for i in range(1, 21)] + [by(99, "promo-net", likes_count=500)]
    with caplog.at_level(logging.INFO, logger="sc_digger.pipeline"):
        kept = score_tracks(tracks, _cfg(["promo-net"]))
    assert 99 not in {t.id for t in kept}
    assert "Gesperrte Accounts aussortiert: 1" in caplog.text


def test_blocked_track_does_not_influence_percentiles():
    base = [by(i, f"artist{i}", likes_count=40 + i) for i in range(1, 21)]
    ref = {t.id: t.percentile for t in score_tracks([by(i, f"artist{i}", likes_count=40 + i)
                                                      for i in range(1, 21)], _cfg([]), apply_filter=False)}
    extra = by(99, "promo-net", likes_count=900)
    got = {t.id: t.percentile for t in score_tracks(base + [extra], _cfg(["promo-net"]))}
    assert all(got[i] == pytest.approx(ref[i]) for i in got)


def test_apply_filter_false_keeps_blocked_accounts():
    tracks = [by(1, "artist1"), by(2, "promo-net")]
    kept = score_tracks(tracks, _cfg(["promo-net"]), apply_filter=False)
    assert {t.id for t in kept} == {1, 2}


def test_without_key_nothing_changes():
    cfg = Config(dict(CFG.raw))
    cfg.raw["scoring"] = {k: v for k, v in CFG["scoring"].items() if k != "blocked_accounts"}
    tracks = [by(i, f"artist{i}") for i in range(1, 11)] + [by(99, "promo-net")]
    assert 99 in {t.id for t in score_tracks(tracks, cfg, apply_filter=False)}
