"""Eigene Tests für Rekordbox-Sterne in der Artist-Reputation (Issue #492)."""
from pathlib import Path

from sc_digger.db import ArtistReputation, TrackDB, TrackRecord
from sc_digger.pipeline import artist_reputation_score


def test_boost_per_star_missing_in_config():
    """Fehlt boost_per_star in der Config, greift der Standardwert 2.0."""
    rep = ArtistReputation(artist="test", stars=4)
    # Leere Config oder Config ohne boost_per_star
    score, note = artist_reputation_score(rep, {"max_boost": 20.0})
    assert score == 8.0
    assert note == "Artist-Bonus: +8"

    # Komplett None
    score2, note2 = artist_reputation_score(rep, None)
    assert score2 == 8.0
    assert note2 == "Artist-Bonus: +8"


def test_stars_capping_with_custom_max():
    """Prüft die Obergrenze max_boost bei hohem Sternewert."""
    rep = ArtistReputation(artist="test", stars=10)
    cfg = {"boost_per_star": 3.0, "max_boost": 15.0}
    # 10 * 3.0 = 30.0 -> gedeckelt auf 15.0
    score, note = artist_reputation_score(rep, cfg)
    assert score == 15.0
    assert note == "Artist-Bonus: +15"


def test_missing_and_zero_rb_rating_in_db(tmp_path: Path):
    """Tracks mit rb_rating NULL, 0 oder negativem Wert zählen nicht in stars."""
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        # a: rb_rating None, like
        db.upsert_track(TrackRecord(path=str(tmp_path / "a.mp3"), mtime=1, size=1, artist="artist1",
                                    status="inbox", rb_rating=None))
        db.set_feedback(str(tmp_path / "a.mp3"), "like")

        # b: rb_rating 0, dislike
        db.upsert_track(TrackRecord(path=str(tmp_path / "b.mp3"), mtime=1, size=1, artist="artist1",
                                    status="inbox", rb_rating=0))
        db.set_feedback(str(tmp_path / "b.mp3"), "dislike")

        # c: rb_rating 0, kein Feedback -> download
        db.upsert_track(TrackRecord(path=str(tmp_path / "c.mp3"), mtime=1, size=1, artist="artist1",
                                    status="inbox", rb_rating=0))

        # d: rb_rating 3, kein Feedback -> stars, kein download
        db.upsert_track(TrackRecord(path=str(tmp_path / "d.mp3"), mtime=1, size=1, artist="artist1",
                                    status="inbox", rb_rating=3))

        rep = db.get_artist_reputations()["artist1"]
        assert rep.stars == 3
        assert rep.likes == 1
        assert rep.dislikes == 1
        assert rep.downloads == 1


def test_stars_prevent_penalty_from_store_feedback(tmp_path: Path):
    """Sterne in tracks verhindern Malus auch wenn store_items viele Dislikes liefert."""
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        # 1 Track mit 2 Sternen
        db.upsert_track(TrackRecord(path=str(tmp_path / "t.mp3"), mtime=1, size=1, artist="store_artist",
                                    status="inbox", rb_rating=2))
        # 5 Dislikes in store_items
        for i in range(5):
            db.upsert_store_item(sc_id=100 + i, title=f"Track {i}", artist="store_artist", purchase_url="http://x")
            db.set_sc_feedback(100 + i, "dislike")

        rep = db.get_artist_reputation("store_artist")
        assert rep.stars == 2
        assert rep.dislikes == 5
        assert rep.likes == 0
        assert rep.downloads == 0

        # Trotz 5 Dislikes greift der Malus nicht, da stars > 0
        cfg = {"boost_per_star": 2.0, "max_boost": 20.0, "min_dislikes_for_penalty": 3, "penalty": 15.0}
        score, note = artist_reputation_score(rep, cfg)
        assert score == 4.0
        assert note == "Artist-Bonus: +4"


def test_unknown_artist_has_zero_stars(tmp_path: Path):
    """Unbekannter Künstler liefert eine leere Reputation mit stars=0."""
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        rep = db.get_artist_reputation("unbekannt")
        assert rep.stars == 0
        assert rep.likes == 0
        assert rep.dislikes == 0
        assert rep.downloads == 0
