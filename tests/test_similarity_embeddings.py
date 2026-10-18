"""Unit-Tests für die Embedding-basierte Ähnlichkeitssuche (Issue #352).

Prüft Cosine-Ähnlichkeitsvergleiche, Filter (Vektorlänge, Min-Score, Exclude-ID),
stabile Sortierung bei Gleichstand, Nullvektoren, Formatierung und Fehlerpfade
von build_similar_by_track.
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.similarity import (
    NO_SIMILAR,
    SIMILAR_FAILED,
    EmbeddingCandidate,
    EmbeddingResult,
    build_similar_by_track,
    find_similar_embeddings,
    format_embedding_list,
)


def _rec(i: int, **kw) -> TrackRecord:
    return TrackRecord(id=i, path=f"/sammlung/{i}.mp3", mtime=1.0, size=1000, **kw)


# ---------------------------------------------------------------------------
# Tests für find_similar_embeddings
# ---------------------------------------------------------------------------


def test_find_similar_embeddings_top_n_zero_or_negative():
    cands = [EmbeddingCandidate(_rec(1), [1.0, 0.0])]
    assert find_similar_embeddings([1.0, 0.0], cands, top_n=0) == []
    assert find_similar_embeddings([1.0, 0.0], cands, top_n=-1) == []


def test_find_similar_embeddings_empty_candidates():
    assert find_similar_embeddings([1.0, 0.0], []) == []


def test_find_similar_embeddings_exclude_track_id_none():
    """exclude_track_id=None schließt keinen Track aus."""
    cands = [
        EmbeddingCandidate(_rec(1), [1.0, 0.0]),
        EmbeddingCandidate(_rec(2), [0.8, 0.6]),
    ]
    results = find_similar_embeddings([1.0, 0.0], cands, exclude_track_id=None)
    assert [r.track.id for r in results] == [1, 2]


def test_find_similar_embeddings_exclude_specific_track():
    cands = [
        EmbeddingCandidate(_rec(10), [1.0, 0.0]),
        EmbeddingCandidate(_rec(20), [0.9, 0.1]),
        EmbeddingCandidate(_rec(30), [0.8, 0.2]),
    ]
    results = find_similar_embeddings([1.0, 0.0], cands, exclude_track_id=20)
    assert [r.track.id for r in results] == [10, 30]


def test_find_similar_embeddings_skips_different_vector_length():
    """Kandidaten mit abweichender Vektorlänge werden ohne Fehler übersprungen."""
    query = [1.0, 0.0, 0.0]
    cands = [
        EmbeddingCandidate(_rec(1), [1.0]),              # zu kurz
        EmbeddingCandidate(_rec(2), [1.0, 0.0, 0.0]),   # passende Länge
        EmbeddingCandidate(_rec(3), [1.0, 0.0, 0.0, 0.0]), # zu lang
        EmbeddingCandidate(_rec(4), []),                 # leer
    ]
    results = find_similar_embeddings(query, cands)
    assert len(results) == 1
    assert results[0].track.id == 2


def test_find_similar_embeddings_null_vector_query():
    """Nullvektor als Query hat mit allen Vektoren Cosine-Ähnlichkeit 0.0."""
    query = [0.0, 0.0]
    cands = [
        EmbeddingCandidate(_rec(1), [1.0, 0.0]),
        EmbeddingCandidate(_rec(2), [0.0, 1.0]),
    ]
    # Standard min_score ist 0.0 -> Score 0.0 >= 0.0, also enthalten
    results = find_similar_embeddings(query, cands, min_score=0.0)
    assert len(results) == 2
    assert results[0].score == pytest.approx(0.0)
    assert results[1].score == pytest.approx(0.0)

    # Bei min_score > 0.0 fliegt Score 0.0 raus
    results_filtered = find_similar_embeddings(query, cands, min_score=0.01)
    assert results_filtered == []


def test_find_similar_embeddings_null_vector_candidate():
    """Nullvektor als Kandidat ergibt Score 0.0."""
    query = [1.0, 0.0]
    cands = [
        EmbeddingCandidate(_rec(1), [0.0, 0.0]),
    ]
    results = find_similar_embeddings(query, cands, min_score=0.0)
    assert len(results) == 1
    assert results[0].score == pytest.approx(0.0)


def test_find_similar_embeddings_min_score_filter():
    """Kandidaten mit Score unter min_score werden übersprungen."""
    query = [1.0, 0.0]
    cands = [
        EmbeddingCandidate(_rec(1), [1.0, 0.0]),   # 1.0
        EmbeddingCandidate(_rec(2), [0.7, 0.7]),   # ~0.707
        EmbeddingCandidate(_rec(3), [0.0, 1.0]),   # 0.0
        EmbeddingCandidate(_rec(4), [-1.0, 0.0]),  # -1.0
    ]
    res_high = find_similar_embeddings(query, cands, min_score=0.8)
    assert [r.track.id for r in res_high] == [1]

    res_negative = find_similar_embeddings(query, cands, min_score=-0.5)
    assert [r.track.id for r in res_negative] == [1, 2, 3]

    res_all = find_similar_embeddings(query, cands, min_score=-1.0)
    assert [r.track.id for r in res_all] == [1, 2, 3, 4]


def test_find_similar_embeddings_stable_sort_on_equal_scores():
    """Gleicher Score erhält die ursprüngliche Reihenfolge der Kandidaten."""
    query = [1.0, 0.0]
    cands = [EmbeddingCandidate(_rec(i), [1.0, 0.0]) for i in [42, 17, 99, 5]]
    results = find_similar_embeddings(query, cands, top_n=10)
    assert [r.track.id for r in results] == [42, 17, 99, 5]


def test_find_similar_embeddings_accepts_generators():
    """find_similar_embeddings akzeptiert beliebige Iterables."""
    query = [1.0, 0.0]
    gen = (EmbeddingCandidate(_rec(i), [1.0, float(i)]) for i in range(5))
    results = find_similar_embeddings(query, gen, top_n=3)
    assert len(results) == 3


# ---------------------------------------------------------------------------
# Tests für format_embedding_list
# ---------------------------------------------------------------------------


def test_format_embedding_list_empty():
    assert format_embedding_list([]) == NO_SIMILAR


def test_format_embedding_list_complete_and_missing_values():
    results = [
        EmbeddingResult(
            _rec(1, artist="DJ Rush", title="Motherfucking Bass", bpm=142.5, key_camelot="9A"),
            score=0.9856,
        ),
        EmbeddingResult(
            _rec(2, artist=None, title=None, bpm=None, key_camelot=None),
            score=0.5,
        ),
        EmbeddingResult(
            _rec(3, artist="Viper XXL", title="Doing Drugs", bpm=150, key_camelot="1A"),
            score=0.0,
        ),
    ]
    text = format_embedding_list(results)
    lines = text.splitlines()
    assert lines[0] == "🎧 Klanglich ähnlich:"
    assert lines[1] == "1. DJ Rush – Motherfucking Bass · 142.5 BPM · 9A · Ähnlichkeit 98.6%"
    assert lines[2] == "2. – – – · – BPM · – · Ähnlichkeit 50.0%"
    assert lines[3] == "3. Viper XXL – Doing Drugs · 150 BPM · 1A · Ähnlichkeit 0.0%"


# ---------------------------------------------------------------------------
# Tests für build_similar_by_track
# ---------------------------------------------------------------------------


def test_build_similar_by_track_returns_none_when_no_embedding(tmp_path: Path):
    with TrackDB(tmp_path / "test.sqlite") as db:
        t = db.upsert_track(TrackRecord(path="/m/1.mp3", mtime=1.0, size=10))
        assert build_similar_by_track(db, t.id) is None


def test_build_similar_by_track_returns_none_for_nonexistent_id(tmp_path: Path):
    with TrackDB(tmp_path / "test.sqlite") as db:
        assert build_similar_by_track(db, 99999) is None


def test_build_similar_by_track_finds_similar_same_model(tmp_path: Path):
    with TrackDB(tmp_path / "test.sqlite") as db:
        t1 = db.upsert_track(TrackRecord(path="/m/1.mp3", mtime=1.0, size=10, artist="A1", title="T1", bpm=140.0, key_camelot="6A"))
        t2 = db.upsert_track(TrackRecord(path="/m/2.mp3", mtime=1.0, size=10, artist="A2", title="T2", bpm=142.0, key_camelot="6A"))
        t3 = db.upsert_track(TrackRecord(path="/m/3.mp3", mtime=1.0, size=10, artist="A3", title="T3", bpm=144.0, key_camelot="7A"))
        t4_diff_model = db.upsert_track(TrackRecord(path="/m/4.mp3", mtime=1.0, size=10, artist="A4", title="T4"))

        db.set_embedding(t1.id, "model_v1", [1.0, 0.0])
        db.set_embedding(t2.id, "model_v1", [0.95, 0.05])
        db.set_embedding(t3.id, "model_v1", [0.6, 0.8])
        db.set_embedding(t4_diff_model.id, "model_other", [1.0, 0.0])

        out = build_similar_by_track(db, t1.id, top_n=2)
        assert out is not None
        lines = out.splitlines()
        assert lines[0] == "🎧 Klanglich ähnlich:"
        assert "A2 – T2" in lines[1]
        assert "A3 – T3" in lines[2]
        # Query-Track selbst und abweichendes Modell dürfen nicht vorkommen
        assert "A1 – T1" not in out
        assert "A4 – T4" not in out


def test_build_similar_by_track_no_other_candidates_gives_no_similar(tmp_path: Path):
    with TrackDB(tmp_path / "test.sqlite") as db:
        t1 = db.upsert_track(TrackRecord(path="/m/1.mp3", mtime=1.0, size=10, artist="Solo", title="Solo Track"))
        db.set_embedding(t1.id, "model_v1", [1.0, 0.0])
        out = build_similar_by_track(db, t1.id)
        assert out == NO_SIMILAR


def test_build_similar_by_track_error_handling_closed_db(tmp_path: Path):
    """Geschlossene oder fehlerhafte DB liefert SIMILAR_FAILED statt Exception."""
    db = TrackDB(tmp_path / "test.sqlite")
    t = db.upsert_track(TrackRecord(path="/m/1.mp3", mtime=1.0, size=10))
    db.set_embedding(t.id, "model_v1", [1.0, 0.0])
    db.close()

    result = build_similar_by_track(db, t.id)
    assert result == SIMILAR_FAILED


def test_build_similar_by_track_error_handling_db_exception(monkeypatch):
    """Unerwartete Fehler in DB-Operationen liefern SIMILAR_FAILED."""
    fake_db = MagicMock()
    fake_db.get_embedding.side_effect = RuntimeError("Disk IO failure")
    result = build_similar_by_track(fake_db, 1)
    assert result == SIMILAR_FAILED
