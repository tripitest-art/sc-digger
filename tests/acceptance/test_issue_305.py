"""Akzeptanztests: Tracks der Sammlung mit SoundCloud-Tracks verknüpfen (reine Logik)."""
import pytest

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.linksource import LinkProposal, apply_proposals, normalize_url, propose_links
from tests.test_modes import mk


def cand(i, title, artist="uploader"):
    return mk(
        i, title=title, permalink_url=f"https://soundcloud.com/{artist}/t{i}",
        user={"username": artist, "permalink_url": f"https://soundcloud.com/{artist}"},
    )


def rec(db, path, **kw):
    return db.upsert_track(TrackRecord(path=path, mtime=1.0, size=1, status="archive", **kw))


@pytest.mark.parametrize("raw, expected", [
    ("https://www.soundcloud.com/A/T1/?si=x#t=1", "https://soundcloud.com/a/t1"),
    ("http://m.soundcloud.com/a/t1/", "https://soundcloud.com/a/t1"),
    ("https://soundcloud.com/a/t1", "https://soundcloud.com/a/t1"),
    ("  ", ""),
    (None, ""),
])
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


def test_unique_match_by_filename(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        r = rec(db, "/sammlung/Svetec - Acid Kick.wav")
        out = propose_links([r], [cand(1, "Svetec - Acid Kick"), cand(2, "Other - Thing")])
    assert out == [LinkProposal(
        record_id=r.id, path="/sammlung/Svetec - Acid Kick.wav", sc_id=1,
        url="https://soundcloud.com/uploader/t1", title="Svetec - Acid Kick", ambiguous=False,
    )]


def test_match_uses_artist_and_title_fields(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        r = rec(db, "/sammlung/01.wav", artist="Svetec", title="Acid Kick")
        out = propose_links([r], [cand(5, "Acid Kick", artist="Svetec")])
    assert [(p.record_id, p.sc_id, p.ambiguous) for p in out] == [(r.id, 5, False)]


def test_different_remix_is_not_matched(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        r = rec(db, "/sammlung/Linkin Park - Numb (Might Be Twins Schranz Remix).wav")
        assert propose_links([r], [cand(1, "Linkin Park - Numb (Schranz Edit)")]) == []


def test_several_candidates_are_ambiguous_first_wins(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        r = rec(db, "/sammlung/Svetec - Acid Kick.wav")
        out = propose_links([r], [cand(10, "Svetec - Acid Kick"), cand(11, "Svetec - Acid Kick")])
    assert len(out) == 1
    assert out[0].sc_id == 10
    assert out[0].ambiguous is True


def test_already_linked_and_unmatched_records_are_skipped(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        linked = rec(db, "/sammlung/Svetec - Acid Kick.wav", source_url="https://soundcloud.com/x/y")
        other = rec(db, "/sammlung/Nichts Passendes.wav")
        assert propose_links([linked, other], [cand(1, "Svetec - Acid Kick")]) == []
        assert propose_links([linked, other], []) == []


def test_apply_writes_only_sure_matches_by_default(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        a = rec(db, "/sammlung/Svetec - Acid Kick.wav")
        b = rec(db, "/sammlung/Viper - Raw Groove.wav")
        props = propose_links([a, b], [
            cand(1, "Svetec - Acid Kick"),
            cand(2, "Viper - Raw Groove"), cand(3, "Viper - Raw Groove"),
        ])
        assert apply_proposals(db, props) == 1
        assert db.get_track_by_id(a.id).source_url == "https://soundcloud.com/uploader/t1"
        assert not db.get_track_by_id(b.id).source_url


def test_apply_with_ambiguous_and_never_overwrites(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        b = rec(db, "/sammlung/Viper - Raw Groove.wav")
        props = propose_links([b], [cand(2, "Viper - Raw Groove"), cand(3, "Viper - Raw Groove")])
        assert apply_proposals(db, props, include_ambiguous=True) == 1
        assert db.get_track_by_id(b.id).source_url == "https://soundcloud.com/uploader/t2"
        # zweiter Lauf: Feld ist belegt, nichts wird überschrieben
        assert apply_proposals(db, props, include_ambiguous=True) == 0
        assert db.get_track_by_id(b.id).source_url == "https://soundcloud.com/uploader/t2"
