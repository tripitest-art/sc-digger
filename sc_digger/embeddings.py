"""Audio-Embeddings: Vektorspeicherung, Jobs und Ähnlichkeitsberechnung (Issue #351).

Verwaltet kompakte float32-Vektoren in SQLite, das Einreihen asynchroner
Embedding-Jobs für die GPU-Workstation und die Auswertung von Embedding-Ergebnissen.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Sequence

from .db import JobType

if TYPE_CHECKING:
    from .db import JobRecord, TrackDB, TrackRecord

Embedder = Callable[[Path], Sequence[float]]


def pack_vector(vector: Sequence[float]) -> bytes:
    """Serialisiert einen float32-Vektor little-endian in ein Byte-Blob.

    Wirft ValueError bei leerem Vektor oder nicht endlichen Werten.
    """
    if not isinstance(vector, (Sequence, list, tuple)):
        raise ValueError("Vektor muss eine Sequenz sein")
    if len(vector) == 0:
        raise ValueError("Vektor darf nicht leer sein")
    for x in vector:
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
            raise ValueError(f"Ungültiger oder nicht-endlicher Vektor-Wert: {x!r}")
    try:
        return struct.pack(f"<{len(vector)}f", *vector)
    except Exception as exc:
        raise ValueError(f"Fehler beim Packen des Vektors: {exc}") from exc


def unpack_vector(blob: bytes) -> list[float]:
    """Deserialisiert ein little-endian float32-Byte-Blob in eine Liste von Floats.

    Wirft ValueError, wenn die Länge nicht durch 4 teilbar ist oder 0 beträgt.
    """
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise ValueError("Blob muss vom Typ bytes sein")
    if len(blob) == 0:
        raise ValueError("Blob darf nicht leer sein")
    if len(blob) % 4 != 0:
        raise ValueError(f"Blob-Länge {len(blob)} ist nicht durch 4 teilbar")
    count = len(blob) // 4
    try:
        return list(struct.unpack(f"<{count}f", blob))
    except Exception as exc:
        raise ValueError(f"Fehler beim Entpacken des Blobs: {exc}") from exc


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Berechnet die Kosinus-Ähnlichkeit zweier Vektoren im Intervall [-1.0, 1.0].

    Wirft ValueError bei unterschiedlicher Vektorlänge oder ungültigen Werten.
    Gibt 0.0 zurück, wenn mindestens einer der Vektoren die Norm 0 hat (Nullvektor).
    """
    if len(a) != len(b):
        raise ValueError(f"Vektoren haben unterschiedliche Längen: {len(a)} != {len(b)}")
    if len(a) == 0:
        return 0.0
    dot = 0.0
    norm_a_sq = 0.0
    norm_b_sq = 0.0
    for x, y in zip(a, b):
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
            raise ValueError(f"Ungültiger Vektor-Wert in a: {x!r}")
        if isinstance(y, bool) or not isinstance(y, (int, float)) or not math.isfinite(y):
            raise ValueError(f"Ungültiger Vektor-Wert in b: {y!r}")
        dot += x * y
        norm_a_sq += x * x
        norm_b_sq += y * y
    norm_a = math.sqrt(norm_a_sq)
    norm_b = math.sqrt(norm_b_sq)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    sim = dot / (norm_a * norm_b)
    return max(-1.0, min(1.0, float(sim)))


def enqueue_embedding_job(db: TrackDB, track: TrackRecord) -> JobRecord | None:
    """Reiht einen Embedding-Job für den Track ein (JobType.EMBEDDING).

    Liefert None, wenn track.id fehlt/None ist oder für den Track bereits ein
    Embedding gespeichert ist.
    Gibt es schon einen anstehenden oder laufenden Job, wird dieser zurückgegeben (kein Duplikat).
    Sonst wird ein neuer Job eingereiht.
    """
    if track.id is None:
        return None
    if db.get_embedding(track.id) is not None:
        return None
    active = db.find_active_job(JobType.EMBEDDING, track.id)
    if active is not None:
        return active
    payload = {"track_id": track.id, "path": track.path, "format": track.format}
    return db.enqueue_job(track.id, JobType.EMBEDDING, payload=payload)


# Ein Track gilt als bewertet, wenn Stephan ihn irgendwo markiert hat. `later` zählt bewusst nicht:
# „später ansehen“ sagt nichts darüber, ob der Track gefällt, und taugt nicht als Trainingslabel.
# Die Digest-Buttons landen in `sc_feedback` (je SoundCloud-ID), nicht in `tracks.feedback`; eine
# übernommene Inbox-Datei liegt meist nicht mehr dort, ihre Archivzeile trägt dieselbe `sc_id`.
_LABELED_SQL = """(
    t.feedback IN ('like', 'dislike')
    OR t.adopted_at IS NOT NULL
    OR t.rb_rating IS NOT NULL
    OR (t.sc_id IS NOT NULL AND (
        EXISTS (SELECT 1 FROM sc_feedback f WHERE f.sc_id = t.sc_id AND f.value IN ('like', 'dislike'))
        OR EXISTS (SELECT 1 FROM tracks o WHERE o.sc_id = t.sc_id AND o.adopted_at IS NOT NULL)
    ))
)"""


@dataclass
class BackfillSummary:
    candidates: int = 0       # Tracks ohne Embedding nach Filter, vor dem Limit
    queued: int = 0           # neu eingereiht (dry_run: würden eingereiht)
    already_queued: int = 0   # offener oder laufender Embedding-Job vorhanden
    missing_file: int = 0     # Datei auf dem Server fehlt


def backfill_embedding_jobs(
    db: TrackDB,
    *,
    labeled_only: bool = True,
    limit: int | None = None,
    dry_run: bool = False,
) -> BackfillSummary:
    """Reiht Embedding-Jobs für vorhandene Tracks ohne Embedding nach.

    `embeddings.auto_queue` erfasst nur neue Inbox-Tracks; bewertete Altbestände (Inbox und
    Sammlung) hätten sonst nie Trainingsdaten für den Taste-Score. Mit `labeled_only` nur
    bewertete Tracks, sonst alle (bewertete zuerst, je Gruppe nach Track-ID). Fehlende Dateien
    und schon aktive Jobs werden gezählt, nicht eingereiht. `limit` begrenzt die neu
    eingereihten Jobs; danach wird nichts mehr gezählt.
    """
    where = "NOT EXISTS (SELECT 1 FROM track_embeddings e WHERE e.track_id = t.id)"
    if labeled_only:
        where += f" AND {_LABELED_SQL}"
    ids = [r[0] for r in db.db.execute(
        f"SELECT t.id FROM tracks t WHERE {where} ORDER BY {_LABELED_SQL} DESC, t.id ASC"
    ).fetchall()]

    summary = BackfillSummary(candidates=len(ids))
    for track_id in ids:
        if limit is not None and summary.queued >= limit:
            break
        track = db.get_track_by_id(track_id)
        if track is None:
            continue
        if not Path(track.path).is_file():
            summary.missing_file += 1
        elif db.find_active_job(JobType.EMBEDDING, track_id) is not None:
            summary.already_queued += 1
        else:
            if not dry_run:
                enqueue_embedding_job(db, track)
            summary.queued += 1
    return summary


def apply_embedding_result(db: TrackDB, job_id: int, result: dict[str, Any]) -> bool:
    """Speichert das Ergebnis eines Embedding-Jobs in der Track-DB.

    result erwartet {"model": str, "vector": Sequence[float]}.
    Gibt False zurück, wenn der Job unbekannt ist, das Modell leer oder der Vektor ungültig ist.
    Gibt True bei erfolgreicher Speicherung zurück.
    """
    if not isinstance(result, dict):
        return False
    model = result.get("model")
    if not isinstance(model, str) or not model.strip():
        return False
    vector = result.get("vector")
    if not isinstance(vector, (list, tuple)) or len(vector) == 0:
        return False
    for x in vector:
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
            return False
    job = db.get_job_by_id(job_id)
    if job is None or job.track_id is None:
        return False
    try:
        db.set_embedding(job.track_id, model, [float(x) for x in vector])
        return True
    except Exception:
        return False


def make_embedding_handler(
    embedder: Embedder,
    fetch_audio: Callable[[dict[str, Any]], Path],
    model: str,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Erzeugt einen Handler für worker.execute_job(handler_map={'embedding': handler}).

    Fehler von fetch_audio oder embedder werden nicht abgefangen, damit execute_job
    sie als 'failed' protokolliert.
    """
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        path = fetch_audio(payload)
        vec = embedder(path)
        return {"model": model, "vector": [float(x) for x in vec]}

    return handler
