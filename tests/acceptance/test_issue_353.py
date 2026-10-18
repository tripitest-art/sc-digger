from pathlib import Path

import pytest

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.taste import (
    MIN_SAMPLES_PER_CLASS,
    TasteModel,
    build_training_set,
    train_taste_model,
)

POS = [[1.0, 1.1], [0.9, 1.0], [1.2, 0.8], [1.1, 1.2], [0.8, 0.9], [1.0, 0.7]]
NEG = [[-1.0, -1.1], [-0.9, -1.0], [-1.2, -0.8], [-1.1, -1.2], [-0.8, -0.9], [-1.0, -0.7]]


def _samples():
    return [(v, 1) for v in POS] + [(v, 0) for v in NEG]


def test_train_separates_clusters():
    model = train_taste_model(_samples())
    assert model.score([1.0, 1.0]) > 0.8
    assert model.score([-1.0, -1.0]) < 0.2
    assert all(0.0 <= model.score(v) <= 1.0 for v in POS + NEG)


def test_train_is_deterministic():
    a = train_taste_model(_samples())
    b = train_taste_model(_samples())
    assert a.to_dict() == b.to_dict()


def test_serialization_roundtrip():
    model = train_taste_model(_samples())
    again = TasteModel.from_dict(model.to_dict())
    assert again.score([0.3, -0.2]) == pytest.approx(model.score([0.3, -0.2]))
    with pytest.raises(ValueError):
        TasteModel.from_dict({"weights": [1.0]})


def test_errors():
    few = [(v, 1) for v in POS[: MIN_SAMPLES_PER_CLASS - 1]] + [(v, 0) for v in NEG]
    with pytest.raises(ValueError):
        train_taste_model(few)
    bad = _samples() + [([1.0, 2.0, 3.0], 1)]
    with pytest.raises(ValueError):
        train_taste_model(bad)
    model = train_taste_model(_samples())
    with pytest.raises(ValueError):
        model.score([1.0])


def test_constant_dimension_does_not_break():
    samples = [([v[0], 5.0], y) for v, y in _samples()]
    model = train_taste_model(samples)
    assert model.score([1.0, 5.0]) > model.score([-1.0, 5.0])


def test_build_training_set(tmp_path: Path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        def add(name, vec, feedback=None, rating=None, adopted=False, model="m"):
            rec = db.upsert_track(TrackRecord(path=f"/m/{name}.mp3", mtime=1.0, size=1, rb_rating=rating))
            if feedback:
                db.set_feedback(rec.id, feedback)
            if adopted:
                db.db.execute("UPDATE tracks SET adopted_at = '2026-10-01 10:00:00' WHERE id = ?", (rec.id,))
                db.db.commit()
            if vec is not None:
                db.set_embedding(rec.id, model, vec)
            return rec.id

        like = add("like", [1.0, 0.0], feedback="like")
        rated = add("rated", [1.0, 0.1], rating=4)
        adopted = add("adopted", [1.0, 0.2], adopted=True)
        dislike = add("dislike", [-1.0, 0.0], feedback="dislike")
        add("dislike_adopted", [-1.0, 0.1], feedback="dislike", adopted=True)
        add("later", [0.0, 0.0], feedback="later")
        add("plain", [0.5, 0.5])
        add("no_embedding", None, feedback="like")
        add("other_model", [9.0, 9.0], feedback="like", model="zzz")
        data = build_training_set(db)
        assert [label for _, label in data] == [1, 1, 1, 0, 1]
        assert data[0][0] == pytest.approx([1.0, 0.0])
        assert like < rated < adopted < dislike
