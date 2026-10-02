"""Akzeptanztests: Artist-Reputation-Score aus Track-DB und Feedback."""
from pathlib import Path

import pytest
import yaml

from sc_digger.db import ArtistReputation, TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.pipeline import artist_reputation_score, score_tracks
from tests.test_modes import mk

ROOT = Path(__file__).resolve().parents[2]
CFG = Config.load(ROOT / "config.yaml")


def test_config_defaults():
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    rep = raw["scoring"]["artist_reputation"]
    assert rep["enabled"] is True
    assert rep["boost_per_like"] == 5.0
    assert rep["boost_per_download"] == 2.0
    assert rep["max_boost"] == 20.0
    assert rep["min_dislikes_for_penalty"] == 3
    assert rep["penalty"] == 15.0


def test_artist_reputation_score_bonus_and_capping():
    cfg = {
        "boost_per_like": 5.0,
        "boost_per_download": 2.0,
        "max_boost": 20.0,
        "min_dislikes_for_penalty": 3,
        "penalty": 15.0,
    }
    # 2 Likes + 1 Download = 10 + 2 = +12
    rep = ArtistReputation(artist="svetec", downloads=1, likes=2, dislikes=0)
    score, note = artist_reputation_score(rep, cfg)
    assert score == 12.0
    assert note == "Artist-Bonus: +12"

    # 10 Downloads = 20 -> max_boost erreicht
    rep_max = ArtistReputation(artist="svetec", downloads=10, likes=2, dislikes=0)
    score, note = artist_reputation_score(rep_max, cfg)
    assert score == 20.0
    assert note == "Artist-Bonus: +20"


def test_artist_reputation_score_penalty():
    cfg = {
        "boost_per_like": 5.0,
        "boost_per_download": 2.0,
        "max_boost": 20.0,
        "min_dislikes_for_penalty": 3,
        "penalty": 15.0,
    }
    # 3 Dislikes, keine Downloads, keine Likes -> Malus
    rep = ArtistReputation(artist="spammer", downloads=0, likes=0, dislikes=3)
    score, note = artist_reputation_score(rep, cfg)
    assert score == -15.0
    assert note == "Artist-Malus: 3 👎"

    # 2 Dislikes (unter Schwelle) -> kein Malus
    rep_under = ArtistReputation(artist="spammer", downloads=0, likes=0, dislikes=2)
    score, note = artist_reputation_score(rep_under, cfg)
    assert score == 0.0
    assert note is None

    # 3 Dislikes aber 1 Like -> kein Malus
    rep_with_like = ArtistReputation(artist="mixed", downloads=0, likes=1, dislikes=3)
    score, note = artist_reputation_score(rep_with_like, cfg)
    assert score == 5.0
    assert note == "Artist-Bonus: +5"


def test_db_aggregates_artist_reputation(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        # Tracks in tracks-Tabelle
        db.upsert_track(TrackRecord(path="/music/a.mp3", mtime=1, size=1, artist="Svetec", status="archive"))
        db.upsert_track(TrackRecord(path="/music/b.mp3", mtime=1, size=1, artist="Svetec", status="inbox"))
        db.upsert_track(TrackRecord(path="/music/c.mp3", mtime=1, size=1, artist=" svetec ", status="inbox"))
        db.set_feedback("/music/c.mp3", "like")
        # Store items + sc_feedback
        db.upsert_store_item(sc_id=123, title="T1", artist="Svetec", purchase_url="https://bandcamp.com/1")
        db.set_sc_feedback(123, "like")

        # Anderer Artist mit Dislikes
        db.upsert_store_item(sc_id=456, title="T2", artist="Bad Artist", purchase_url="https://bandcamp.com/2")
        db.set_sc_feedback(456, "dislike")
        db.upsert_track(TrackRecord(path="/music/d.mp3", mtime=1, size=1, artist="Bad Artist", status="rejected"))
        db.set_feedback("/music/d.mp3", "dislike")

        rep_map = db.get_artist_reputations()
        assert "svetec" in rep_map
        s_rep = rep_map["svetec"]
        assert s_rep.downloads == 2  # a, b (c hat feedback='like')
        assert s_rep.likes == 2      # c in tracks + 123 in store_items
        assert s_rep.dislikes == 0

        single = db.get_artist_reputation("Svetec")
        assert single == s_rep

        bad_rep = rep_map["bad artist"]
        assert bad_rep.dislikes == 2
        assert bad_rep.downloads == 0


def test_score_tracks_applies_reputation_bonus_and_malus(tmp_path):
    db_file = tmp_path / "tracks.sqlite"
    with TrackDB(db_file) as db:
        for i in range(5):
            db.upsert_track(TrackRecord(path=f"/music/fav_{i}.mp3", mtime=1, size=1, artist="FavArtist", status="archive"))
        for i in range(3):
            p = f"/music/bad_{i}.mp3"
            db.upsert_track(TrackRecord(path=p, mtime=1, size=1, artist="HatedArtist", status="rejected"))
            db.set_feedback(p, "dislike")

    cfg_dict = dict(CFG.raw)
    cfg_dict["state"] = {**CFG["state"], "track_db_path": str(db_file)}
    cfg_dict["scoring"] = {
        **CFG["scoring"],
        "artist_reputation": {
            "enabled": True,
            "boost_per_like": 5.0,
            "boost_per_download": 2.0,
            "max_boost": 20.0,
            "min_dislikes_for_penalty": 3,
            "penalty": 15.0,
        },
    }
    cfg = Config(cfg_dict)

    neutral = mk(1, playback_count=1000, likes_count=50, user={"username": "NormalArtist", "permalink_url": ""})
    fav = mk(2, playback_count=1000, likes_count=50, user={"username": "FavArtist", "permalink_url": ""})
    hated = mk(3, playback_count=1000, likes_count=50, user={"username": "HatedArtist", "permalink_url": ""})

    scored = score_tracks([neutral, fav, hated], cfg, apply_filter=False)
    by_id = {t.id: t for t in scored}

    assert by_id[2].score > by_id[1].score
    assert any("Artist-Bonus" in n for n in by_id[2].notes)
    assert by_id[3].score < by_id[1].score
    assert any("Artist-Malus" in n for n in by_id[3].notes)


def test_score_tracks_disabled_reputation_has_no_effect(tmp_path):
    db_file = tmp_path / "tracks.sqlite"
    with TrackDB(db_file) as db:
        for i in range(5):
            db.upsert_track(TrackRecord(path=f"/music/fav_{i}.mp3", mtime=1, size=1, artist="FavArtist", status="archive"))

    cfg_dict = dict(CFG.raw)
    cfg_dict["state"] = {**CFG["state"], "track_db_path": str(db_file)}
    cfg_dict["scoring"] = {
        **CFG["scoring"],
        "artist_reputation": {"enabled": False},
    }
    cfg = Config(cfg_dict)

    neutral = mk(1, playback_count=1000, likes_count=50, user={"username": "NormalArtist", "permalink_url": ""})
    fav = mk(2, playback_count=1000, likes_count=50, user={"username": "FavArtist", "permalink_url": ""})

    scored = score_tracks([neutral, fav], cfg, apply_filter=False)
    by_id = {t.id: t for t in scored}
    assert by_id[1].score == by_id[2].score
