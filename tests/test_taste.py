"""Unit-Tests für sc_digger/taste.py (Issue #353)."""
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


def test_weights_sign_on_separable_data():
    """Bei Daten, bei denen Dimension 0 positiv für Klasse 1 und negativ für Klasse 0 ist,
    muss das gelernte Gewicht w[0] positiv sein."""
    # Klasse 1 hat x[0] > 0, Klasse 0 hat x[0] < 0
    samples = [
        ([10.0, 0.0], 1),
        ([9.0, 0.0], 1),
        ([8.0, 0.0], 1),
        ([7.0, 0.0], 1),
        ([6.0, 0.0], 1),
        ([-10.0, 0.0], 0),
        ([-9.0, 0.0], 0),
        ([-8.0, 0.0], 0),
        ([-7.0, 0.0], 0),
        ([-6.0, 0.0], 0),
    ]
    model = train_taste_model(samples, epochs=100, lr=0.1)
    assert model.weights[0] > 0.5
    # Zweite Dimension trägt nicht zur Trennung bei
    assert abs(model.weights[1]) < abs(model.weights[0])


def test_l2_regularization_shrinks_weights():
    """Ein größerer L2-Regularisierungsparameter muss die Gewichte dämpfen (kleinere Beträge)."""
    samples = _samples()
    model_low_l2 = train_taste_model(samples, epochs=300, lr=0.1, l2=0.0001)
    model_high_l2 = train_taste_model(samples, epochs=300, lr=0.1, l2=1.0)

    norm_low = sum(w**2 for w in model_low_l2.weights)
    norm_high = sum(w**2 for w in model_high_l2.weights)
    assert norm_high < norm_low


def test_epochs_zero_yields_half_score():
    """Bei epochs=0 bleiben Gewichte und Bias 0, Sigmoid(0) = 0.5 für beliebige Eingabevektoren."""
    samples = _samples()
    model = train_taste_model(samples, epochs=0)
    assert model.weights == [0.0, 0.0]
    assert model.bias == 0.0
    assert model.score([1.0, 1.0]) == pytest.approx(0.5)
    assert model.score([-5.0, 2.0]) == pytest.approx(0.5)


def test_build_training_set_empty_db(tmp_path: Path):
    """build_training_set auf einer leeren Datenbank gibt eine leere Liste zurück."""
    with TrackDB(tmp_path / "empty.sqlite") as db:
        data = build_training_set(db)
        assert data == []


def test_build_training_set_tie_breaking_alphabetical(tmp_path: Path):
    """Bei Gleichstand der Modell-Häufigkeit gewinnt das alphabetisch kleinere Modell."""
    with TrackDB(tmp_path / "tie.sqlite") as db:
        # 5 Tracks für model_b
        for i in range(5):
            rec = db.upsert_track(TrackRecord(path=f"/m/b_{i}.mp3", mtime=1.0, size=1))
            db.set_feedback(rec.id, "like")
            db.set_embedding(rec.id, "model_b", [float(i), 1.0])

        # 5 Tracks für model_a (Gleichstand mit model_b, model_a ist alphabetisch kleiner)
        for i in range(5):
            rec = db.upsert_track(TrackRecord(path=f"/m/a_{i}.mp3", mtime=1.0, size=1))
            db.set_feedback(rec.id, "like")
            db.set_embedding(rec.id, "model_a", [float(i), 2.0])

        data = build_training_set(db)
        # Alle 5 müssen model_a sein (vector[1] == 2.0)
        assert len(data) == 5
        assert all(v[1] == 2.0 for v, _ in data)


def test_taste_model_validation_errors():
    """TasteModel.score und from_dict werfen erwartete ValueErrors bei Fehleingaben."""
    model = TasteModel(weights=[1.0, -1.0], bias=0.0, mean=[0.0, 0.0], std=[1.0, 1.0])

    # Falsche Vektorlänge
    with pytest.raises(ValueError):
        model.score([1.0])
    with pytest.raises(ValueError):
        model.score([1.0, 2.0, 3.0])

    # Ungültige Werte im Vektor
    with pytest.raises(ValueError):
        model.score([float("nan"), 1.0])
    with pytest.raises(ValueError):
        model.score([float("inf"), 1.0])
    with pytest.raises(ValueError):
        model.score(["bad", 1.0])

    # from_dict mit fehlenden Schlüsseln
    with pytest.raises(ValueError):
        TasteModel.from_dict("not a dict")
    with pytest.raises(ValueError):
        TasteModel.from_dict({"weights": [1.0], "bias": 0.0, "mean": [0.0]})  # std fehlt
    with pytest.raises(ValueError):
        TasteModel.from_dict({"weights": [1.0], "bias": "bad", "mean": [0.0], "std": [1.0]})

    # Uneinheitliche Längen in from_dict
    with pytest.raises(ValueError):
        TasteModel.from_dict({"weights": [1.0, 2.0], "bias": 0.0, "mean": [0.0], "std": [1.0]})


def test_train_taste_model_validation_errors():
    """train_taste_model validiert Eingaben streng."""
    # Leere Samples
    with pytest.raises(ValueError):
        train_taste_model([])

    # Ungültiges Label (weder 0 noch 1)
    samples = _samples() + [([1.0, 1.0], 2)]
    with pytest.raises(ValueError):
        train_taste_model(samples)

    # Uneinheitliche Vektorlänge
    samples_bad_dim = _samples() + [([1.0, 1.0, 1.0], 0)]
    with pytest.raises(ValueError):
        train_taste_model(samples_bad_dim)
