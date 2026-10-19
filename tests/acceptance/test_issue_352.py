from pathlib import Path

import pytest

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.similarity import (
    NO_SIMILAR,
    EmbeddingCandidate,
    build_similar_by_track,
    find_similar_embeddings,
    format_embedding_list,
)


def _rec(i: int, **kw) -> TrackRecord:
    return TrackRecord(id=i, path=f"/m/{i}.mp3", mtime=1.0, size=1, **kw)


def test_find_similar_orders_and_filters():
    cands = [
        EmbeddingCandidate(_rec(1), [0.0, 1.0]),
        EmbeddingCandidate(_rec(2), [1.0, 0.1]),
        EmbeddingCandidate(_rec(3), [1.0, 0.0]),
        EmbeddingCandidate(_rec(4), [1.0, 0.0, 0.0]),   # falsche Länge
        EmbeddingCandidate(_rec(5), [-1.0, 0.0]),
    ]
    res = find_similar_embeddings([1.0, 0.0], cands, top_n=3)
    assert [r.track.id for r in res] == [3, 2, 1]
    assert res[0].score == pytest.approx(1.0)
    res = find_similar_embeddings([1.0, 0.0], cands, exclude_track_id=3, top_n=1)
    assert [r.track.id for r in res] == [2]
    res = find_similar_embeddings([1.0, 0.0], cands, min_score=0.5)
    assert [r.track.id for r in res] == [3, 2]
    assert find_similar_embeddings([1.0, 0.0], cands, top_n=0) == []
    assert find_similar_embeddings([1.0, 0.0], []) == []


def test_find_similar_ties_keep_candidate_order():
    cands = [EmbeddingCandidate(_rec(i), [1.0, 0.0]) for i in (7, 8, 9)]
    assert [r.track.id for r in find_similar_embeddings([1.0, 0.0], cands)] == [7, 8, 9]


def test_format_embedding_list():
    assert format_embedding_list([]) == NO_SIMILAR
    res = find_similar_embeddings(
        [1.0, 0.0],
        [EmbeddingCandidate(_rec(1, artist="A", title="T", bpm=150.0, key_camelot="8A"), [1.0, 0.0]),
         EmbeddingCandidate(_rec(2), [1.0, 1.0])],
    )
    lines = format_embedding_list(res).splitlines()
    assert lines[0] == "🎧 Klanglich ähnlich:"
    assert lines[1] == "1. A – T · 150.0 BPM · 8A · Ähnlichkeit 100.0%"
    assert lines[2] == "2. – – – · – BPM · – · Ähnlichkeit 70.7%"


def test_build_similar_by_track(tmp_path: Path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        a = db.upsert_track(TrackRecord(path="/m/a.mp3", mtime=1.0, size=1, artist="A", title="Query"))
        b = db.upsert_track(TrackRecord(path="/m/b.mp3", mtime=1.0, size=1, artist="B", title="Nah"))
        c = db.upsert_track(TrackRecord(path="/m/c.mp3", mtime=1.0, size=1, artist="C", title="Fremd"))
        assert build_similar_by_track(db, a.id) is None
        db.set_embedding(a.id, "m1", [1.0, 0.0])
        db.set_embedding(b.id, "m1", [0.9, 0.1])
        db.set_embedding(c.id, "m2", [1.0, 0.0])      # anderes Modell -> ausgelassen
        text = build_similar_by_track(db, a.id)
        assert "B – Nah" in text
        assert "Query" not in text and "Fremd" not in text
