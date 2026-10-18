"""Taste-Score: Logistische Regression auf Audio-Embeddings und Feedback (Issue #353).

Ermöglicht das Trainieren eines linearen Modells auf Vektoren (z. B. CLMR/Mule-Embeddings),
um die Wahrscheinlichkeit (0.0 .. 1.0) vorherzusagen, dass ein neuer Track gefällt.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Sequence

import numpy as np

if TYPE_CHECKING:
    from .db import TrackDB

MIN_SAMPLES_PER_CLASS: int = 5


@dataclass(frozen=True)
class TasteModel:
    weights: list[float]
    bias: float
    mean: list[float]  # Standardisierung je Dimension
    std: list[float]  # Dimensionen mit Streuung 0 werden mit 1.0 geteilt

    def score(self, vector: Sequence[float]) -> float:
        """Wahrscheinlichkeit 0..1, dass der Track gefällt. ValueError bei falscher Vektorlänge."""
        if len(vector) != len(self.weights):
            raise ValueError(
                f"Vektorlänge {len(vector)} stimmt nicht mit Modell-Dimension {len(self.weights)} überein"
            )
        for x in vector:
            if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
                raise ValueError(f"Ungültiger oder nicht-endlicher Vektor-Wert: {x!r}")

        # Standardisierung je Dimension (Streuung 0 -> std=1.0)
        z = self.bias
        for v, m, s, w in zip(vector, self.mean, self.std, self.weights):
            scale = s if s > 0 else 1.0
            x_std = (float(v) - m) / scale
            z += w * x_std

        # Sigmoid mit numerischem Clipping gegen Overflow
        z_clipped = max(-50.0, min(50.0, z))
        prob = 1.0 / (1.0 + math.exp(-z_clipped))
        return float(prob)

    def to_dict(self) -> dict[str, Any]:
        """Gibt ein Dictionary mit ausschließlich JSON-fähigen Basistypen zurück."""
        return {
            "weights": [float(w) for w in self.weights],
            "bias": float(self.bias),
            "mean": [float(m) for m in self.mean],
            "std": [float(s) for s in self.std],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TasteModel":
        """ValueError bei fehlenden Schlüsseln oder uneinheitlichen Längen."""
        if not isinstance(data, dict):
            raise ValueError(f"Dictionary erwartet, got {type(data).__name__}")

        required_keys = ("weights", "bias", "mean", "std")
        for key in required_keys:
            if key not in data:
                raise ValueError(f"Schlüssel '{key}' fehlt im Dictionary")

        weights = data["weights"]
        mean = data["mean"]
        std = data["std"]
        bias = data["bias"]

        if not isinstance(weights, (list, tuple)):
            raise ValueError("weights muss eine Liste oder ein Tupel sein")
        if not isinstance(mean, (list, tuple)):
            raise ValueError("mean muss eine Liste oder ein Tupel sein")
        if not isinstance(std, (list, tuple)):
            raise ValueError("std muss eine Liste oder ein Tupel sein")
        if isinstance(bias, bool) or not isinstance(bias, (int, float)) or not math.isfinite(bias):
            raise ValueError(f"Ungültiger bias: {bias!r}")

        dim = len(weights)
        if len(mean) != dim or len(std) != dim:
            raise ValueError(
                f"Uneinheitliche Dimensionen: weights={dim}, mean={len(mean)}, std={len(std)}"
            )

        weights_clean = [float(w) for w in weights]
        mean_clean = [float(m) for m in mean]
        std_clean = [float(s) for s in std]

        return cls(
            weights=weights_clean,
            bias=float(bias),
            mean=mean_clean,
            std=std_clean,
        )


def train_taste_model(
    samples: Sequence[tuple[Sequence[float], int]],
    *,
    epochs: int = 500,
    lr: float = 0.1,
    l2: float = 0.01,
) -> TasteModel:
    """Batch-Gradientenabstieg auf standardisierten Vektoren, deterministisch (keine Zufallszahlen,
    Startgewichte 0). Label 1 = gefällt, 0 = gefällt nicht. ValueError, wenn eine Klasse
    weniger als MIN_SAMPLES_PER_CLASS Beispiele hat oder die Vektorlängen abweichen.
    """
    if not samples:
        raise ValueError("Samples dürfen nicht leer sein")

    dim = len(samples[0][0])
    for idx, (vec, label) in enumerate(samples):
        if len(vec) != dim:
            raise ValueError(
                f"Vektor an Index {idx} hat abweichende Dimension {len(vec)} (erwartet: {dim})"
            )
        if label not in (0, 1):
            raise ValueError(f"Ungültiges Label {label!r} an Index {idx} (erwartet: 0 oder 1)")

    pos = [s for s in samples if s[1] == 1]
    neg = [s for s in samples if s[1] == 0]

    if len(pos) < MIN_SAMPLES_PER_CLASS:
        raise ValueError(
            f"Zu wenige positive Beispiele: {len(pos)} < {MIN_SAMPLES_PER_CLASS}"
        )
    if len(neg) < MIN_SAMPLES_PER_CLASS:
        raise ValueError(
            f"Zu wenige negative Beispiele: {len(neg)} < {MIN_SAMPLES_PER_CLASS}"
        )

    X = np.array([s[0] for s in samples], dtype=np.float64)
    y = np.array([s[1] for s in samples], dtype=np.float64)
    n_samples = len(samples)

    mean = np.mean(X, axis=0)
    std = np.std(X, axis=0)

    # Dimensionen mit Streuung 0 werden mit 1.0 geteilt
    std_safe = np.where(std > 0, std, 1.0)
    X_std = (X - mean) / std_safe

    w = np.zeros(dim, dtype=np.float64)
    b = 0.0

    if epochs > 0:
        for _ in range(epochs):
            z = np.dot(X_std, w) + b
            z_clipped = np.clip(z, -50.0, 50.0)
            y_hat = 1.0 / (1.0 + np.exp(-z_clipped))
            error = y_hat - y

            dw = (1.0 / n_samples) * np.dot(X_std.T, error) + float(l2) * w
            db = (1.0 / n_samples) * float(np.sum(error))

            w -= float(lr) * dw
            b -= float(lr) * db

    return TasteModel(
        weights=[float(x) for x in w],
        bias=float(b),
        mean=[float(x) for x in mean],
        std=[float(x) for x in std_safe],
    )


def build_training_set(db: TrackDB) -> list[tuple[list[float], int]]:
    """Positiv (1): Tracks mit gespeichertem Embedding und feedback = 'like', adopted_at gesetzt
    oder rb_rating >= 4. Negativ (0): feedback = 'dislike' (nur, wenn weder adopted_at gesetzt
    noch rb_rating >= 4). 'later', fehlendes Feedback und Tracks ohne Embedding bleiben draußen.
    Nur Embeddings des Modells, das am häufigsten vorkommt (bei Gleichstand das
    alphabetisch kleinere). Reihenfolge aufsteigend nach Track-ID.
    """
    from .embeddings import unpack_vector

    rows = db.db.execute(
        """
        SELECT t.id, t.feedback, t.adopted_at, t.rb_rating, e.model, e.vector
        FROM tracks t
        JOIN track_embeddings e ON t.id = e.track_id
        WHERE e.vector IS NOT NULL
        ORDER BY t.id ASC
        """
    ).fetchall()

    qualified_tracks: list[tuple[int, str, list[float], int]] = []
    model_counter: Counter[str] = Counter()

    for row in rows:
        track_id = int(row["id"])
        feedback = row["feedback"]
        adopted_at = row["adopted_at"]
        rb_rating = row["rb_rating"]
        model = str(row["model"])
        raw_vector = row["vector"]

        # Positiv (1): feedback == 'like', adopted_at gesetzt oder rb_rating >= 4
        is_pos = (
            (adopted_at is not None)
            or (rb_rating is not None and rb_rating >= 4)
            or (feedback == "like")
        )

        if is_pos:
            label = 1
        elif feedback == "dislike":
            # Negativ (0): feedback == 'dislike' (nur wenn weder adopted_at noch rb_rating >= 4)
            label = 0
        else:
            continue

        vector = unpack_vector(raw_vector)
        qualified_tracks.append((track_id, model, vector, label))
        model_counter[model] += 1

    if not qualified_tracks:
        return []

    # Modell mit meisten Vorkommen, bei Gleichstand alphabetisch kleineres
    dominant_model = min(model_counter.keys(), key=lambda m: (-model_counter[m], m))

    return [
        (vec, label)
        for _, model, vec, label in qualified_tracks
        if model == dominant_model
    ]
