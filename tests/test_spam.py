"""Unit-Tests für Spam-Signal-Erkennung und Score-Abzug (Issue #77)."""
from pathlib import Path
import pytest

from sc_digger.models import Config, Track
from sc_digger.pipeline import score_tracks, spam_signals
from tests.test_modes import mk

ROOT = Path(__file__).resolve().parents[1]
CFG = Config.load(ROOT / "config.yaml")


def test_boundary_values_reposts_likes():
    """Grenzwerte für Bedingung 1: reposts >= 10 und reposts > 3 * likes."""
    # Genau 3 * likes (z. B. 12 Reposts, 4 Likes -> 12 > 12 ist False) -> kein Treffer
    t_equal = mk(1, likes_count=4, reposts_count=12, comment_count=5)
    assert spam_signals(t_equal, []) == []

    # 10 Reposts, 3 Likes (10 > 9) -> Treffer
    t_hit = mk(2, likes_count=3, reposts_count=10, comment_count=5)
    assert spam_signals(t_hit, []) == ["Reposts > 3× Likes"]

    # 9 Reposts, 2 Likes (9 > 6, aber reposts < 10) -> kein Treffer
    t_under_min = mk(3, likes_count=2, reposts_count=9, comment_count=5)
    assert spam_signals(t_under_min, []) == []


def test_boundary_values_reposts_comments():
    """Grenzwerte für Bedingung 2: reposts >= 50 und comments <= 1."""
    # Genau 50 Reposts, genau 1 Kommentar -> Treffer
    t_50_1 = mk(1, likes_count=100, reposts_count=50, comment_count=1)
    assert spam_signals(t_50_1, []) == ["viele Reposts, kaum Kommentare"]

    # 49 Reposts, 0 Kommentare -> kein Treffer
    t_49 = mk(2, likes_count=100, reposts_count=49, comment_count=0)
    assert spam_signals(t_49, []) == []

    # 50 Reposts, 2 Kommentare -> kein Treffer
    t_2_comments = mk(3, likes_count=100, reposts_count=50, comment_count=2)
    assert spam_signals(t_2_comments, []) == []


def test_none_fields_and_empty_tags_do_not_crash():
    """None-Felder oder leere Tags führen nicht zu Fehlern."""
    t = Track(
        id=1,
        url="https://soundcloud.com/a/1",
        artist="Artist",
        artist_url="",
        created_at="2026-09-29T10:00:00Z",
        duration_ms=300000,
        genre="",
        tags=[],
        description="",
        bpm=None,
        plays=1000,
        likes=0,
        reposts=0,
        comments=0,
        downloadable=False,
        has_downloads_left=False,
        purchase_url=None,
        purchase_title=None,
        title="",
    )
    # Manuell auf None setzen für Grenzfalltests
    t.title = None
    t.description = None
    t.tags = None
    t.reposts = None
    t.likes = None
    t.comments = None

    assert spam_signals(t, ["repost exchange"]) == []


def test_config_without_spam_keys_preserves_score():
    """Fehlen spam_penalty und spam_phrases in der Config, bleibt das Verhalten unverändert."""
    cfg_raw = dict(CFG.raw)
    cfg_raw["scoring"] = {k: v for k, v in CFG["scoring"].items() if not k.startswith("spam_")}
    cfg = Config(cfg_raw)

    tracks = [
        mk(1, likes_count=50, reposts_count=5, description="repost exchange"),
        mk(2, likes_count=50, reposts_count=5),
    ]
    scored = score_tracks(tracks, cfg, apply_filter=False)
    # Beide Tracks haben identische Metriken -> identische Scores ohne Abzug
    assert scored[0].score == pytest.approx(scored[1].score)
    assert not any("Promo-Verdacht" in n for t in scored for n in t.notes)


def test_apply_filter_false_applies_penalty():
    """Auch bei apply_filter=False (On-Demand-Check) wird der Spam-Abzug berechnet und notiert."""
    cfg = Config(dict(CFG.raw))
    cfg.raw["scoring"] = {
        **CFG["scoring"],
        "spam_penalty": 25,
        "spam_phrases": ["promo service"],
    }
    t_clean = mk(1, likes_count=100, reposts_count=10)
    t_spam = mk(2, likes_count=100, reposts_count=10, description="Check our PROMO SERVICE now")

    scored = score_tracks([t_clean, t_spam], cfg, apply_filter=False)
    by_id = {t.id: t for t in scored}
    assert by_id[2].score == pytest.approx(by_id[1].score - 25)
    assert any("Promo-Verdacht" in n and "promo service" in n for n in by_id[2].notes)
