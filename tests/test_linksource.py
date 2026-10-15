"""Tests zu :mod:`sc_digger.linksource` (Logik aus Issue #305).

Ergänzt die Akzeptanztests: Randfälle, die im Issue unter „Fertig, wenn“ genannt sind.
"""
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.linksource import apply_proposals, propose_links
from tests.test_modes import mk


def cand(i, title, artist="uploader"):
    return mk(
        i, title=title, permalink_url=f"https://soundcloud.com/{artist}/t{i}",
        user={"username": artist, "permalink_url": f"https://soundcloud.com/{artist}"},
    )


def rec(db, path, **kw):
    return db.upsert_track(TrackRecord(path=path, mtime=1.0, size=1, status="archive", **kw))


def test_filename_with_leading_soundcloud_id(tmp_path):
    """„[12345] Artist - Titel.wav“: die vorangestellte ID stört den Vergleich nicht."""
    with TrackDB(tmp_path / "t.sqlite") as db:
        r = rec(db, "/sammlung/[12345] Svetec - Acid Kick.wav")
        out = propose_links([r], [cand(1, "Svetec - Acid Kick")])
    assert [(p.record_id, p.sc_id, p.ambiguous) for p in out] == [(r.id, 1, False)]


def test_record_without_id_is_skipped():
    record = TrackRecord(path="/sammlung/Svetec - Acid Kick.wav", mtime=1.0, size=1)
    assert record.id is None
    assert propose_links([record], [cand(1, "Svetec - Acid Kick")]) == []


def test_empty_candidate_list_proposes_nothing(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        r = rec(db, "/sammlung/Svetec - Acid Kick.wav")
        assert propose_links([r], []) == []


def test_apply_without_proposals_writes_nothing(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        r = rec(db, "/sammlung/Svetec - Acid Kick.wav")
        assert apply_proposals(db, []) == 0
        assert not db.get_track_by_id(r.id).source_url
