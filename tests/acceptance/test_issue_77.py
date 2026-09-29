"""Akzeptanztests: Promo-Netzwerk-Verdacht senkt den Score."""
from pathlib import Path

import pytest
import yaml

from sc_digger.models import Config
from sc_digger.pipeline import score_tracks, spam_signals
from tests.test_modes import mk

ROOT = Path(__file__).resolve().parents[2]
CFG = Config.load(ROOT / "config.yaml")
PHRASES = ["repost exchange", "send your tracks"]


def _cfg(penalty=20, phrases=PHRASES):
    cfg = Config(dict(CFG.raw))
    cfg.raw["scoring"] = {**CFG["scoring"], "spam_penalty": penalty, "spam_phrases": list(phrases)}
    return cfg


def test_config_defaults():
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert raw["scoring"]["spam_penalty"] == 20
    phrases = [p.lower() for p in raw["scoring"]["spam_phrases"]]
    assert "repost exchange" in phrases and "send your tracks" in phrases


def test_normal_track_has_no_signals():
    assert spam_signals(mk(1, likes_count=50, reposts_count=5, comment_count=1), PHRASES) == []


def test_repost_heavy_track():
    t = mk(1, likes_count=10, reposts_count=40, comment_count=3)
    assert spam_signals(t, PHRASES) == ["Reposts > 3× Likes"]


def test_few_reposts_are_not_suspicious():
    # 9 Reposts bei 2 Likes: zu wenig Daten für einen Verdacht
    assert spam_signals(mk(1, likes_count=2, reposts_count=9, comment_count=0), PHRASES) == []


def test_many_reposts_without_comments():
    t = mk(1, likes_count=200, reposts_count=60, comment_count=1)
    assert spam_signals(t, PHRASES) == ["viele Reposts, kaum Kommentare"]


def test_promo_phrase_in_description_title_or_tags():
    t = mk(1, description="Free DL! Join our REPOST EXCHANGE group")
    assert spam_signals(t, PHRASES) == ["Promo-Text: repost exchange"]
    t = mk(2, title="Banger (send your tracks)")
    assert spam_signals(t, PHRASES) == ["Promo-Text: send your tracks"]
    t = mk(3, tag_list='"repost exchange" schranz')
    assert spam_signals(t, PHRASES) == ["Promo-Text: repost exchange"]


def test_signals_in_fixed_order_and_one_phrase_only():
    t = mk(1, likes_count=10, reposts_count=60, comment_count=0,
           description="send your tracks / repost exchange")
    assert spam_signals(t, PHRASES) == ["Reposts > 3× Likes", "viele Reposts, kaum Kommentare",
                                        "Promo-Text: repost exchange"]


def test_score_penalty_once_and_note():
    clean = [mk(i, likes_count=50, reposts_count=5) for i in range(1, 11)]
    spam = mk(99, likes_count=50, reposts_count=5, description="repost exchange")
    by_id = {t.id: t for t in score_tracks(clean + [spam], _cfg(penalty=0), apply_filter=False)}
    base = by_id[99].score

    clean = [mk(i, likes_count=50, reposts_count=5) for i in range(1, 11)]
    spam = mk(99, likes_count=50, reposts_count=5, description="repost exchange")
    by_id = {t.id: t for t in score_tracks(clean + [spam], _cfg(penalty=20), apply_filter=False)}
    assert by_id[99].score == pytest.approx(base - 20)
    assert any("Promo-Verdacht" in n and "repost exchange" in n for n in by_id[99].notes)
    assert all(not any("Promo-Verdacht" in n for n in by_id[i].notes) for i in range(1, 11))


def test_penalty_drops_track_below_percentile():
    tracks = [mk(i, likes_count=50, reposts_count=5) for i in range(1, 21)]
    tracks.append(mk(99, likes_count=50, reposts_count=5, description="send your tracks"))
    kept = {t.id for t in score_tracks(tracks, _cfg(penalty=20))}
    assert 99 not in kept
