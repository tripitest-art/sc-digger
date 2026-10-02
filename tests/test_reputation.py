"""Zusätzliche Tests für den Artist-Reputation-Score (Issue #107)."""
from pathlib import Path

from sc_digger.db import ArtistReputation, TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.pipeline import artist_reputation_score, score_tracks
from tests.test_modes import mk

ROOT = Path(__file__).resolve().parents[1]
CFG = Config.load(ROOT / "config.yaml")


def test_artist_reputation_score_none_and_defaults():
    # None-Reputation -> neutral, keine Notiz
    assert artist_reputation_score(None) == (0.0, None)
    # Standardwerte greifen ohne cfg
    rep = ArtistReputation(artist="x", downloads=1, likes=1, dislikes=0)
    assert artist_reputation_score(rep) == (7.0, "Artist-Bonus: +7")
    # Unbekannter Künstler (leere Reputation) -> neutral
    assert artist_reputation_score(ArtistReputation(artist="x")) == (0.0, None)


def test_artist_reputation_score_partial_override_uses_defaults():
    # Nur ein Schlüssel gesetzt: die restlichen Defaults greifen
    rep = ArtistReputation(artist="x", downloads=0, likes=0, dislikes=3)
    assert artist_reputation_score(rep, {"penalty": 42.0}) == (-42.0, "Artist-Malus: 3 👎")
    boon = ArtistReputation(artist="x", downloads=0, likes=2, dislikes=0)
    # boost_per_like default 5.0 -> +10
    assert artist_reputation_score(boon, {"max_boost": 10.0}) == (10.0, "Artist-Bonus: +10")


def test_db_normalizes_spaces_and_special_characters(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.upsert_track(TrackRecord(path="/music/e.mp3", mtime=1, size=1, artist=" O.B.I. ", status="inbox"))
        db.upsert_store_item(sc_id=1, title="T", artist="o.b.i.", purchase_url="https://bandcamp.com/1")
        db.set_sc_feedback(1, "like")

        rep_map = db.get_artist_reputations()
        assert "o.b.i." in rep_map
        rep = rep_map["o.b.i."]
        assert rep.downloads == 1
        assert rep.likes == 1

        # Lookup über beliebige Schreibweise trifft denselben Eintrag
        assert db.get_artist_reputation("  O.B.I.  ") == rep
        assert db.get_artist_reputation("o.b.i.") == rep


def test_db_ignores_none_and_blank_artists(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.upsert_track(TrackRecord(path="/music/n.mp3", mtime=1, size=1, artist=None, status="inbox"))
        db.upsert_track(TrackRecord(path="/music/b.mp3", mtime=1, size=1, artist="   ", status="inbox"))
        assert db.get_artist_reputations() == {}


def test_get_artist_reputation_unknown_and_none(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        assert db.get_artist_reputation("Niemand") == ArtistReputation(artist="niemand")
        assert db.get_artist_reputation(None) == ArtistReputation(artist="")
        assert db.get_artist_reputation("") == ArtistReputation(artist="")


def test_score_tracks_with_missing_db_is_unchanged(tmp_path):
    cfg_dict = dict(CFG.raw)
    cfg_dict["state"] = {
        **CFG["state"],
        "track_db_path": str(tmp_path / "does_not_exist.sqlite"),
    }
    cfg = Config(cfg_dict)

    tracks = [
        mk(1, playback_count=1000, likes_count=50, user={"username": "A", "permalink_url": ""}),
        mk(2, playback_count=1000, likes_count=50, user={"username": "B", "permalink_url": ""}),
    ]
    scored = score_tracks(list(tracks), cfg, apply_filter=False)
    by_id = {t.id: t for t in scored}
    # kein Bonus/Malus, keine Notizen
    assert by_id[1].score == by_id[2].score
    assert not any("Artist-" in n for t in scored for n in t.notes)


def test_score_tracks_missing_state_key_does_not_crash(tmp_path):
    cfg_dict = dict(CFG.raw)
    cfg_dict.pop("state", None)
    cfg_dict["scoring"] = {
        **CFG["scoring"],
        "artist_reputation": {"enabled": True},
    }
    cfg = Config(cfg_dict)
    tracks = [mk(1, playback_count=1000, likes_count=50)]
    scored = score_tracks(tracks, cfg, apply_filter=False)
    assert len(scored) == 1
