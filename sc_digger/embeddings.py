"""Audio-Embeddings: Vektorspeicherung, Jobs und Ähnlichkeitsberechnung (Issue #351).

Verwaltet kompakte float32-Vektoren in SQLite, das Einreihen asynchroner
Embedding-Jobs für die GPU-Workstation und die Auswertung von Embedding-Ergebnissen.
"""
from __future__ import annotations

import importlib.util
import logging
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Sequence

from .db import JobType

if TYPE_CHECKING:
    from .db import JobRecord, TrackDB, TrackRecord

log = logging.getLogger("sc_digger.embeddings")

Embedder = Callable[[Path], Sequence[float]]

MFCC_EMBEDDING_MODEL = "mfcc-stat-v1"
MAEST_EMBEDDING_MODEL = "discogs-maest-30s-pw-129e"
MAEST_HF_MODEL_ID = "mtg-upf/discogs-maest-30s-pw-129e"
# Feste Revision: das Modell läuft mit trust_remote_code, fremder Code darf sich nicht still ändern.
MAEST_HF_REVISION = "e5d670316c61b18aaef46ce66242fcd73870d792"
MAEST_SAMPLE_RATE = 16000
MAEST_EMBEDDING_LAYER = 7  # Token-Mittelwert dieses Transformer-Blocks

_MFCC_SAMPLE_RATE = 22050


class EmbedderUnavailable(RuntimeError):
    """Der gewünschte Embedder lässt sich auf diesem Rechner nicht betreiben (Pakete fehlen)."""


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


def make_mfcc_embedder(n_mfcc: int = 20) -> Embedder:
    """Deterministischer MFCC-Embedder (Mittelwert und Std je Koeffizient, 2*n_mfcc Floats).

    Läuft ohne GPU und ohne Zusatzpakete; gedacht als Fallback für Tests und Server.
    Nicht-endliche Werte (z. B. bei Stille) werden zu 0.0, eine leere Datei ergibt ValueError.
    """
    def embed(path: Path) -> list[float]:
        # librosa erst hier importieren: der Import ist langsam und nur für diesen Embedder nötig.
        import librosa
        import numpy as np

        try:
            samples, sr = librosa.load(str(path), sr=_MFCC_SAMPLE_RATE, mono=True)
        except Exception as exc:  # noqa: BLE001 – leere/kaputte Datei einheitlich melden
            if Path(path).is_file() and Path(path).stat().st_size == 0:
                raise ValueError(f"Audiodatei ist leer: {path}") from exc
            raise
        if len(samples) == 0:
            raise ValueError(f"Audiodatei ist leer: {path}")
        mfcc = librosa.feature.mfcc(y=samples, sr=sr, n_mfcc=n_mfcc)
        values = np.concatenate([mfcc.mean(axis=1), mfcc.std(axis=1)])
        values = np.where(np.isfinite(values), values, 0.0)
        return [float(x) for x in values]

    return embed


def split_windows(samples: Any, sr: int, window_s: float = 30.0, max_windows: int = 8) -> list:
    """Schneidet bis zu ``max_windows`` gleichmäßig verteilte Fenster aus ``samples``.

    MAEST sieht je Durchlauf nur ``window_s`` Sekunden; ein ganzer Track wird deshalb über
    mehrere Fenster abgetastet (erstes am Anfang, letztes am Ende). Leer ergibt ``[]``, ein
    Track höchstens einer Fensterlänge ein Fenster mit allen Samples.
    """
    total = len(samples)
    if total == 0:
        return []
    size = round(window_s * sr)
    if total <= size:
        return [samples]
    count = max(1, min(max_windows, total // size))
    if count == 1:
        start = (total - size) // 2
        return [samples[start:start + size]]
    starts = [round(i * (total - size) / (count - 1)) for i in range(count)]
    return [samples[start:start + size] for start in starts]


def _load_audio_16k(path: Path) -> Any:
    """Standard-Lader für MAEST: Mono bei 16 kHz."""
    import librosa

    samples, _sr = librosa.load(str(path), sr=MAEST_SAMPLE_RATE, mono=True)
    return samples


def _load_maest_window_fn() -> Callable[[Any], Sequence[float]]:
    """Lädt MAEST und liefert eine Funktion Fenster -> Vektor (nur auf der Workstation).

    Der Import von torch/transformers steht bewusst nur hier, damit Server und Tests ohne
    diese Pakete auskommen. ``cuda`` gilt auch für ROCm-Builds von torch.
    """
    import torch
    from transformers import AutoFeatureExtractor, AutoModel

    device = "cuda" if torch.cuda.is_available() else "cpu"
    extractor = AutoFeatureExtractor.from_pretrained(
        MAEST_HF_MODEL_ID, revision=MAEST_HF_REVISION, trust_remote_code=True
    )
    model = AutoModel.from_pretrained(
        MAEST_HF_MODEL_ID, revision=MAEST_HF_REVISION, trust_remote_code=True
    )
    model.to(device)
    model.eval()
    log.info("MAEST geladen (Gerät: %s)", device)

    def window_fn(window: Any) -> Sequence[float]:
        inputs = extractor(window, sampling_rate=MAEST_SAMPLE_RATE, return_tensors="pt")
        inputs = {key: value.to(device) for key, value in inputs.items()}
        with torch.no_grad():
            outputs = model(**inputs, output_hidden_states=True)
        hidden = outputs.hidden_states[MAEST_EMBEDDING_LAYER]
        return hidden.mean(dim=1)[0].float().cpu().numpy().tolist()

    return window_fn


def make_maest_embedder(
    *,
    window_fn_factory: Callable[[], Callable[[Any], Sequence[float]]] | None = None,
    load_audio: Callable[[Path], Any] | None = None,
    window_s: float = 30.0,
    max_windows: int = 8,
) -> Embedder:
    """MAEST-Embedder: Fenster einzeln rechnen, mitteln und auf Länge 1 normieren.

    Ohne ``window_fn_factory`` müssen torch und transformers installiert sein, sonst
    ``EmbedderUnavailable``. Das Modell wird erst beim ersten Embedding geladen und danach
    wiederverwendet (das Laden dauert; der Worker soll dafür nicht beim Start warten).
    """
    if window_fn_factory is None:
        missing = [
            name for name in ("torch", "transformers") if importlib.util.find_spec(name) is None
        ]
        if missing:
            raise EmbedderUnavailable(f"Paket(e) fehlen: {', '.join(missing)}")
        window_fn_factory = _load_maest_window_fn
    loader = load_audio or _load_audio_16k
    state: dict[str, Any] = {}

    def embed(path: Path) -> list[float]:
        import numpy as np

        windows = split_windows(loader(path), MAEST_SAMPLE_RATE, window_s, max_windows)
        if not windows:
            raise ValueError(f"Audiodatei ist leer: {path}")
        if "window_fn" not in state:
            state["window_fn"] = window_fn_factory()
        window_fn = state["window_fn"]
        vectors = np.asarray([window_fn(window) for window in windows], dtype=np.float64)
        mean = vectors.mean(axis=0)
        if not np.all(np.isfinite(mean)):
            raise ValueError("Embedding enthält nicht-endliche Werte")
        norm = float(np.linalg.norm(mean))
        if norm > 0.0:
            mean = mean / norm
        return [float(x) for x in mean]

    return embed


def select_embedder(name: str) -> tuple[Embedder, str] | None:
    """Embedder samt Modellname für ``--embedder``; ``none`` ergibt ``None``.

    Ruft ``make_maest_embedder`` über den Modulnamen auf, damit Tests ihn ersetzen können.
    """
    if name == "maest":
        return make_maest_embedder(), MAEST_EMBEDDING_MODEL
    if name == "mfcc":
        return make_mfcc_embedder(), MFCC_EMBEDDING_MODEL
    if name == "none":
        return None
    raise ValueError(f"Unbekannter Embedder {name!r} (erlaubt: maest, mfcc, none)")
