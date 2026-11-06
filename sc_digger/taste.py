"""Taste-Score: Logistische Regression auf Audio-Embeddings und Feedback (Issue #353).

Ermöglicht das Trainieren eines linearen Modells auf Vektoren (z. B. CLMR/Mule-Embeddings),
um die Wahrscheinlichkeit (0.0 .. 1.0) vorherzusagen, dass ein neuer Track gefällt.
"""
from __future__ import annotations

import json
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


@dataclass(frozen=True)
class TasteRun:
    trained: bool
    embedding_model: str | None
    n_pos: int
    n_neg: int
    scored: int
    reason: str            # "" wenn trained


def training_data(db: TrackDB) -> tuple[str | None, list[tuple[list[float], int]]]:
    """Sammelt alle Bewertungsquellen als Trainingsbeispiele.

    Positiv (1), wenn mindestens eins gilt: adopted_at gesetzt, rb_rating >= 4,
    feedback = 'like', sc_feedback.value = 'like' für t.sc_id oder eine andere Zeile
    mit derselben sc_id ist übernommen (Archivzeile). Positiv gewinnt immer.
    Negativ (0) nur, wenn nicht positiv und feedback oder sc_feedback 'dislike' ist.
    'later', Unbewertete und Tracks ohne Embedding bleiben draußen. Liefert das
    dominante Embedding-Modell (bei Gleichstand alphabetisch kleiner) und die
    Beispiele aufsteigend nach Track-ID; ohne Beispiele (None, []).
    """
    from .embeddings import unpack_vector

    rows = db.db.execute(
        """
        SELECT t.id, t.feedback, t.adopted_at, t.rb_rating, t.sc_id,
               e.model, e.vector, sf.value AS sc_value,
               EXISTS(
                   SELECT 1 FROM tracks a
                   WHERE a.sc_id = t.sc_id AND a.adopted_at IS NOT NULL
               ) AS sc_adopted
        FROM tracks t
        JOIN track_embeddings e ON t.id = e.track_id
        LEFT JOIN sc_feedback sf ON sf.sc_id = t.sc_id
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
        sc_value = row["sc_value"]
        sc_adopted = bool(row["sc_adopted"])
        model = str(row["model"])
        raw_vector = row["vector"]

        is_pos = (
            (adopted_at is not None)
            or (rb_rating is not None and rb_rating >= 4)
            or (feedback == "like")
            or (sc_value == "like")
            or sc_adopted
        )

        if is_pos:
            label = 1
        elif feedback == "dislike" or sc_value == "dislike":
            label = 0
        else:
            continue

        vector = unpack_vector(raw_vector)
        qualified_tracks.append((track_id, model, vector, label))
        model_counter[model] += 1

    if not qualified_tracks:
        return None, []

    # Modell mit meisten Vorkommen, bei Gleichstand alphabetisch kleineres
    dominant_model = min(model_counter.keys(), key=lambda m: (-model_counter[m], m))

    samples = [
        (vec, label)
        for _, model, vec, label in qualified_tracks
        if model == dominant_model
    ]
    return dominant_model, samples


def build_training_set(db: TrackDB) -> list[tuple[list[float], int]]:
    """Trainingsbeispiele aus :func:`training_data` (alle Bewertungsquellen)."""
    return training_data(db)[1]


def save_taste_model(db: TrackDB, model: TasteModel, embedding_model: str, *,
                     n_pos: int, n_neg: int, trained_at: str) -> None:
    """Ersetzt die einzige Modellzeile (id = 1) samt Metadaten und JSON."""
    db.db.execute(
        """
        INSERT OR REPLACE INTO taste_model
            (id, embedding_model, trained_at, n_pos, n_neg, model_json)
        VALUES (1, ?, ?, ?, ?, ?)
        """,
        (embedding_model, trained_at, n_pos, n_neg,
         json.dumps(model.to_dict())),
    )
    db.db.commit()


def load_taste_model(db: TrackDB) -> tuple[str, TasteModel] | None:
    """Liest das gespeicherte Modell; None ohne Zeile oder bei unlesbarem JSON."""
    row = db.db.execute(
        "SELECT embedding_model, model_json FROM taste_model WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    try:
        model = TasteModel.from_dict(json.loads(row["model_json"]))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return str(row["embedding_model"]), model


def score_tracks(db: TrackDB, model: TasteModel, embedding_model: str) -> int:
    """Schreibt taste_score für alle Tracks in einer Transaktion.

    Nur Embeddings desselben Modells mit passender Dimension bekommen einen Score;
    alle anderen Zeilen (kein Embedding, anderes Modell, falsche Dimension) werden NULL.
    Gibt die Zahl der bewerteten Tracks zurück.
    """
    from .embeddings import unpack_vector

    rows = db.db.execute(
        """
        SELECT t.id AS track_id, e.model AS model, e.vector AS vector
        FROM tracks t
        LEFT JOIN track_embeddings e ON e.track_id = t.id
        ORDER BY t.id ASC
        """
    ).fetchall()

    updates: list[tuple[float | None, int]] = []
    scored = 0
    for row in rows:
        score: float | None = None
        if row["vector"] is not None and row["model"] == embedding_model:
            vector = unpack_vector(row["vector"])
            if len(vector) == len(model.weights):
                try:
                    score = model.score(vector)
                    scored += 1
                except ValueError:
                    score = None
        updates.append((score, int(row["track_id"])))

    with db.db:
        db.db.executemany(
            "UPDATE tracks SET taste_score = ? WHERE id = ?",
            updates,
        )
    return scored


def run_taste(db: TrackDB, *, now: str, dry_run: bool = False) -> TasteRun:
    """Trainiert den Taste-Score und schreibt Modell und Scores.

    Unter MIN_SAMPLES_PER_CLASS je Klasse wird nichts geändert (altes Modell und
    alte Scores bleiben). dry_run schreibt weder Modell noch Scores.
    """
    embedding_model, samples = training_data(db)
    n_pos = sum(1 for _, label in samples if label == 1)
    n_neg = sum(1 for _, label in samples if label == 0)

    if n_pos < MIN_SAMPLES_PER_CLASS or n_neg < MIN_SAMPLES_PER_CLASS:
        return TasteRun(
            False, embedding_model, n_pos, n_neg, 0,
            f"zu wenige Beispiele: {n_pos} gefällt, {n_neg} gefällt nicht "
            f"(mindestens {MIN_SAMPLES_PER_CLASS} je Klasse)",
        )

    model = train_taste_model(samples)
    assert embedding_model is not None  # samples nicht leer => Modell bekannt

    if dry_run:
        row = db.db.execute(
            "SELECT COUNT(*) FROM track_embeddings WHERE model = ?",
            (embedding_model,),
        ).fetchone()
        scored = int(row[0])
    else:
        save_taste_model(db, model, embedding_model, n_pos=n_pos, n_neg=n_neg,
                         trained_at=now)
        scored = score_tracks(db, model, embedding_model)

    return TasteRun(True, embedding_model, n_pos, n_neg, scored, "")
