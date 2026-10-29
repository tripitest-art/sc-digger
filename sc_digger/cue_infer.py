"""Cue-Inferenz für den GPU-Worker (Issue #227, Teil 2).

Der Worker bekommt über die Job-Queue ``cues``-Jobs, lädt die Audiodatei (lokal oder per
``GET /api/jobs/{id}/audio``) und schickt die fertigen Cue-Zeiten als Ergebnis zurück.
Die Umrechnung von Segmenten in Cue-Kennzahlen steckt bewusst in ``extract_cues_from_segments``:
Sie ist rein rechnerisch, ohne Audio-Bibliotheken und damit im Test ohne GPU prüfbar.
"""
from __future__ import annotations

import logging
import math
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator

from .embeddings import Embedder
from .structure import analyze_structure, find_first_kick

if TYPE_CHECKING:  # Nur für Typen; zur Laufzeit vermeidet das den Import-Zyklus zu worker.py.
    from .worker import JobWorkerClient

log = logging.getLogger("sc_digger.cue_infer")

# Struktur-Segmente aus structure.analyze_structure() sind nach Kickmustern benannt
# ("main"), die Cue-Terminologie der Job-Queue spricht von "drop". Der erste Main-Abschnitt
# nach dem Intro ist der erste Drop, ein weiterer Main-Abschnitt nach einem Breakdown der
# zweite Drop.
_LABEL_BY_KIND = {
    "intro": "intro",
    "main": "drop",
    "breakdown": "breakdown",
    "outro": "outro",
}

_DROP_LABELS = ("drop", "chorus")
_BREAKDOWN_LABELS = ("breakdown", "bridge")


def _finite(value: Any) -> float | None:
    """Wert als endliche Zahl oder None; wirft nie."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _normalize_segments(segments: Any) -> list[tuple[float, float, str]]:
    """Segmente als (start, end, label), zeitlich sortiert. Ungültige Einträge entfallen."""
    normalized: list[tuple[float, float, str]] = []
    if not isinstance(segments, (list, tuple)):
        return normalized
    for raw in segments:
        if not isinstance(raw, dict):
            continue
        start = _finite(raw.get("start"))
        if start is None:
            continue
        end = _finite(raw.get("end"))
        if end is None:
            end = start
        label = str(raw.get("label") or "").strip().lower()
        normalized.append((start, max(start, end), label))
    normalized.sort(key=lambda item: item[0])
    return normalized


def _first_start(segments: list[tuple[float, float, str]], labels: tuple[str, ...]) -> float | None:
    """Startzeit des ersten Segments mit einem der labels, sonst None."""
    for start, _end, label in segments:
        if label in labels:
            return round(start, 3)
    return None


def _build_cues(
    first_kick_s: float,
    drop_s: float | None,
    drop2_s: float | None,
    breakdown_s: float | None,
) -> list[dict[str, Any]]:
    """Cue-Liste in der Form, die ``cues.apply_cue_result`` in die Track-DB schreibt."""
    cues: list[dict[str, Any]] = [{"name": "First Kick", "time": first_kick_s, "type": "cue"}]
    if drop_s is not None:
        cues.append({"name": "Drop", "time": drop_s, "type": "cue"})
    if drop2_s is not None:
        cues.append({"name": "Drop 2", "time": drop2_s, "type": "cue"})
    if breakdown_s is not None:
        cues.append({"name": "Breakdown", "time": breakdown_s, "type": "cue"})
    return cues


def extract_cues_from_segments(
    segments: list[dict[str, Any]],
    beats: list[float] | None = None,
    duration: float = 0.0,
) -> dict[str, Any]:
    """Wandelt Struktur-Segmente und Beats in DJ-Cue-Punkte um.

    Die Regeln stehen im Issue #354: First Kick ist der erste Beat ab dem ersten
    Nicht-Silence-Segment, Drop/Drop 2 der Beginn der Drop- bzw. Chorus-Segmente,
    Breakdown der Beginn von ``breakdown``/``bridge``. Fehlende Angaben ergeben
    Standardwerte statt Fehlern – ein kaputter Job soll nicht den Worker beenden.
    """
    empty: dict[str, Any] = {
        "first_kick_s": 0.0,
        "drop_s": None,
        "drop2_s": None,
        "breakdown_s": None,
        "breakdown_kind": "groove",
        "breakdown_ratio": 0.0,
        "cues": [],
    }
    try:
        ordered = _normalize_segments(segments)

        beat_times: list[float] = []
        for raw in beats or []:
            beat = _finite(raw)
            if beat is not None and beat >= 0:
                beat_times.append(beat)
        beat_times.sort()

        non_silence_start = next(
            (start for start, _end, label in ordered if label != "silence"), None
        )
        if not beat_times:
            first_kick_s = 0.0
        else:
            reference = non_silence_start if non_silence_start is not None else 0.0
            first_kick_s = next(
                (beat for beat in beat_times if beat >= reference - 1e-9), reference
            )
            first_kick_s = round(first_kick_s, 3)

        drop_labels = [start for start, _end, label in ordered if label in _DROP_LABELS]
        drop_s = round(drop_labels[0], 3) if drop_labels else None
        pure_drops = [start for start, _end, label in ordered if label == "drop"]
        drop2_s = round(pure_drops[1], 3) if len(pure_drops) > 1 else None
        breakdown_s = _first_start(ordered, _BREAKDOWN_LABELS)
        breakdown_kind = "silence" if any(
            label == "silence" for _start, _end, label in ordered
        ) else "groove"

        breakdown_seconds = sum(
            end - start for start, end, label in ordered if label in _BREAKDOWN_LABELS
        )
        total = _finite(duration) or 0.0
        breakdown_ratio = round(breakdown_seconds / max(total, 1.0), 3)

        has_input = bool(ordered) or bool(beat_times)
        return {
            "first_kick_s": first_kick_s,
            "drop_s": drop_s,
            "drop2_s": drop2_s,
            "breakdown_s": breakdown_s,
            "breakdown_kind": breakdown_kind,
            "breakdown_ratio": breakdown_ratio,
            "cues": _build_cues(first_kick_s, drop_s, drop2_s, breakdown_s) if has_input else [],
        }
    except Exception as exc:  # noqa: BLE001 – Cue-Berechnung darf nie werfen
        log.warning("Cue-Berechnung fehlgeschlagen: %s", exc)
        return empty


def infer_cues_from_audio(path: Path) -> dict[str, Any]:
    """Berechnet Cues aus einer Audiodatei mit der lokalen Strukturanalyse.

    Der Worker läuft auf der Workstation ohne Sammlung und ohne Stem-Trennung, deshalb
    stützt sich die Inferenz auf ``find_first_kick`` und ``analyze_structure``. Die
    Breakdown-Art bleibt ``groove``; ohne Drum-Stem lässt sich Stille nicht sicher
    unterscheiden. Fehler der Analyse führen zu Standardwerten (die Analyse wirft selbst nie).
    """
    audio_path = Path(path)
    segments = [
        {"start": segment.start_s, "end": segment.end_s,
         "label": _LABEL_BY_KIND.get(segment.kind, segment.kind)}
        for segment in analyze_structure(audio_path)
    ]
    first_kick = find_first_kick(audio_path)
    beats = [first_kick] if first_kick is not None else []
    duration = segments[-1]["end"] if segments else 0.0
    return extract_cues_from_segments(segments, beats=beats, duration=duration)


def _local_audio_path(payload: dict[str, Any]) -> Path | None:
    """Erster vorhandener lokaler Pfad aus ``local_path``/``path``, sonst None."""
    for key in ("local_path", "path"):
        value = payload.get(key)
        if not isinstance(value, (str, Path)) or not str(value):
            continue
        candidate = Path(value)
        if candidate.is_file():
            return candidate
    return None


def _audio_suffix(payload: dict[str, Any]) -> str:
    """Dateiendung für die temporäre Download-Datei, aus ``payload['format']``."""
    raw = str(payload.get("format") or "").strip().lstrip(".").lower()
    if raw and raw.isalnum():
        return f".{raw}"
    return ".audio"


@contextmanager
def _job_audio(
    payload: dict[str, Any], client: "JobWorkerClient | None", kind: str
) -> Iterator[Path]:
    """Liefert den Pfad der Audiodatei eines Jobs (``kind`` nur für Fehlermeldungen).

    Lokale Dateien werden direkt genutzt und nie gelöscht. Fehlt die Datei auf der
    Workstation (der Payload-Pfad ist ein Serverpfad), lädt ``client.get_job_audio`` die
    Rohdaten in eine temporäre Datei, die im ``finally`` gelöscht wird, auch bei Fehlern.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"{kind}-Payload ist kein Objekt")

    audio_path = _local_audio_path(payload)
    temp_path: Path | None = None
    try:
        if audio_path is None:
            if client is None:
                raise FileNotFoundError(
                    f"Audiodatei nicht gefunden und kein Client zum Download: {payload.get('path')!r}"
                )
            job_id = payload.get("job_id")
            if job_id is None:
                raise ValueError(f"{kind}-Payload enthält keine job_id für den Audio-Download")
            data = client.get_job_audio(int(job_id))
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=_audio_suffix(payload)
            ) as handle:
                handle.write(data)
                temp_path = Path(handle.name)
            audio_path = temp_path
        yield audio_path
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError as exc:  # noqa: BLE001 – Aufräumen darf das Ergebnis nicht kippen
                log.warning("Temporäre Audiodatei %s nicht gelöscht: %s", temp_path, exc)


def handle_cues_job(
    payload: dict[str, Any],
    client: "JobWorkerClient | None" = None,
    *,
    infer_fn: Callable[[Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Führt einen ``cues``-Job aus: Audiodatei besorgen, Inferenz rechnen, aufräumen.

    Die Audiodatei kommt lokal oder per Download (siehe ``_job_audio``). Fehler der
    Inferenz werden durchgereicht und von ``execute_job`` zu ``failed``.
    """
    infer = infer_fn or infer_cues_from_audio
    with _job_audio(payload, client, "cues") as audio_path:
        return infer(audio_path)


def handle_embedding_job(
    payload: dict[str, Any],
    client: "JobWorkerClient | None" = None,
    *,
    embedder: Embedder,
    model: str,
) -> dict[str, Any]:
    """Führt einen ``embedding``-Job aus; Ergebnis ``{"model": ..., "vector": [...]}``.

    Audio besorgen wie bei ``cues`` (lokal oder per Download, temporäre Datei wird
    gelöscht). Das Format passt zu ``embeddings.apply_embedding_result``.
    """
    with _job_audio(payload, client, "embedding") as audio_path:
        vector = embedder(audio_path)
    return {"model": model, "vector": [float(x) for x in vector]}


def build_handler_map(
    client: "JobWorkerClient | None" = None,
    infer_fn: Callable[[Path], dict[str, Any]] | None = None,
    *,
    embedder: Embedder | None = None,
    embedding_model: str | None = None,
) -> dict[str, Callable[[dict[str, Any]], Any]]:
    """Standard-Handler-Mapping für ``execute_job``/``run_worker_loop``.

    Der ``cues``-Handler wird mit dem übergebenen Client und der optionalen Inferenz
    gebunden; Tests ersetzen die GPU-Inferenz so durch eine reine Funktion. Der
    ``embedding``-Handler kommt nur dazu, wenn ein ``embedder`` übergeben wird; ohne
    Modellname würde das Ergebnis ohne Herkunft gespeichert, daher ValueError.
    """
    if embedder is not None and not (isinstance(embedding_model, str) and embedding_model.strip()):
        raise ValueError("embedder braucht ein nicht-leeres embedding_model")

    def _handle_cues(payload: dict[str, Any]) -> dict[str, Any]:
        return handle_cues_job(payload, client=client, infer_fn=infer_fn)

    handlers: dict[str, Callable[[dict[str, Any]], Any]] = {"cues": _handle_cues}
    if embedder is not None:
        model = str(embedding_model)

        def _handle_embedding(payload: dict[str, Any]) -> dict[str, Any]:
            return handle_embedding_job(payload, client=client, embedder=embedder, model=model)

        handlers["embedding"] = _handle_embedding
    return handlers
