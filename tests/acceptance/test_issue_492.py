"""Akzeptanztests: Rekordbox-Sterne zählen in der Artist-Reputation positiv."""
from sc_digger.db import ArtistReputation, TrackDB, TrackRecord
from sc_digger.pipeline import artist_reputation_score

CFG = {
    "boost_per_like": 5.0,
    "boost_per_download": 2.0,
    "boost_per_star": 2.0,
    "max_boost": 20.0,
    "min_dislikes_for_penalty": 3,
    "penalty": 15.0,
}


def test_stars_default_is_zero():
    assert ArtistReputation(artist="x").stars == 0


def test_stars_give_bonus():
    rep = ArtistReputation(artist="svetec", stars=5)
    score, note = artist_reputation_score(rep, CFG)
    assert score == 10.0
    assert note == "Artist-Bonus: +10"


def test_stars_add_to_likes_and_downloads():
    # 1 Like (5) + 1 Download (2) + 3 Sterne (6) = 13
    rep = ArtistReputation(artist="svetec", likes=1, downloads=1, stars=3)
    score, note = artist_reputation_score(rep, CFG)
    assert score == 13.0
    assert note == "Artist-Bonus: +13"


def test_stars_are_capped_by_max_boost():
    rep = ArtistReputation(artist="svetec", stars=20)
    score, note = artist_reputation_score(rep, CFG)
    assert score == 20.0
    assert note == "Artist-Bonus: +20"


def test_boost_per_star_has_default_two():
    rep = ArtistReputation(artist="svetec", stars=3)
    score, _ = artist_reputation_score(rep, None)
    assert score == 6.0
    score, _ = artist_reputation_score(rep, {"max_boost": 20.0})
    assert score == 6.0


def test_stars_prevent_penalty():
    rep = ArtistReputation(artist="mixed", dislikes=3, stars=1)
    score, note = artist_reputation_score(rep, CFG)
    assert score == 2.0
    assert note == "Artist-Bonus: +2"


def test_penalty_without_stars_unchanged():
    rep = ArtistReputation(artist="spammer", dislikes=3)
    score, note = artist_reputation_score(rep, CFG)
    assert score == -15.0
    assert note == "Artist-Malus: 3 👎"


def test_db_aggregates_stars(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.upsert_track(TrackRecord(path=str(tmp_path / "a.mp3"), mtime=1, size=1, artist=" Svetec ",
                                    status="inbox", rb_rating=4))
        db.upsert_track(TrackRecord(path=str(tmp_path / "b.mp3"), mtime=1, size=1, artist="svetec",
                                    status="inbox", rb_rating=2))
        db.upsert_track(TrackRecord(path=str(tmp_path / "c.mp3"), mtime=1, size=1, artist="svetec",
                                    status="inbox"))
        db.upsert_track(TrackRecord(path=str(tmp_path / "d.mp3"), mtime=1, size=1, artist="svetec",
                                    status="inbox", rb_rating=0))
        db.upsert_track(TrackRecord(path=str(tmp_path / "e.mp3"), mtime=1, size=1, artist="svetec",
                                    status="inbox", rb_rating=5))
        db.set_feedback(str(tmp_path / "b.mp3"), "dislike")
        db.set_feedback(str(tmp_path / "e.mp3"), "like")

        rep = db.get_artist_reputations()["svetec"]
        # Sterne gewinnen gegen Feedback: a(4) + b(2, trotz 👎) + e(5, trotz 👍)
        assert rep.stars == 11
        assert rep.likes == 0
        assert rep.dislikes == 0
        # c ohne Rating und d mit rb_rating = 0 bleiben normale Downloads
        assert rep.downloads == 2
        assert db.get_artist_reputation("Svetec") == rep
