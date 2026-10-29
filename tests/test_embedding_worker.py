"""Eigene Tests zu Issue #395: Embedding-Handler, MFCC-/MAEST-Embedder und job_id im Worker."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from sc_digger import embeddings as emb
from sc_digger.cue_infer import handle_cues_job, handle_embedding_job
from sc_digger.embeddings import (
    MAEST_EMBEDDING_LAYER,
    MAEST_HF_REVISION,
    make_maest_embedder,
    make_mfcc_embedder,
    select_embedder,
    split_windows,
)
from sc_digger.worker import _with_job_id, execute_job


def test_with_job_id_ergaenzt_fehlende_id():
    job = {"id": 3, "job_type": "cues", "payload": {"path": "x"}}
    result = _with_job_id(job)
    assert result["payload"] == {"path": "x", "job_id": 3}
    # Das Original bleibt unberührt (der Job geht auch an Logzeilen und Heartbeat).
    assert job["payload"] == {"path": "x"}


def test_with_job_id_laesst_vorhandene_id_stehen():
    job = {"id": 3, "payload": {"job_id": 99}}
    assert _with_job_id(job) is job
    assert _with_job_id(job)["payload"]["job_id"] == 99


def test_with_job_id_ohne_payload_objekt_oder_id_unveraendert():
    kein_dict = {"id": 3, "payload": "kaputt"}
    assert _with_job_id(kein_dict) is kein_dict
    ohne_payload = {"id": 3}
    assert _with_job_id(ohne_payload) is ohne_payload
    ohne_id = {"payload": {"path": "x"}}
    assert _with_job_id(ohne_id) is ohne_id


def test_maest_embedder_wirft_bei_leerem_audio_und_laedt_dann_kein_modell(tmp_path: Path):
    loads: list[int] = []

    def factory():
        loads.append(1)
        return lambda window: [1.0]

    embed = make_maest_embedder(window_fn_factory=factory, load_audio=lambda p: np.zeros(0, dtype=np.float32))
    with pytest.raises(ValueError):
        embed(tmp_path / "leer.wav")
    assert loads == []


def test_maest_embedder_wirft_bei_nicht_endlichem_mittelwert(tmp_path: Path):
    embed = make_maest_embedder(
        window_fn_factory=lambda: (lambda window: [float("nan"), 1.0]),
        load_audio=lambda p: np.ones(16000, dtype=np.float32),
    )
    with pytest.raises(ValueError):
        embed(tmp_path / "x.wav")


def test_maest_embedder_nullvektor_bleibt_unveraendert(tmp_path: Path):
    embed = make_maest_embedder(
        window_fn_factory=lambda: (lambda window: [0.0, 0.0]),
        load_audio=lambda p: np.ones(16000, dtype=np.float32),
    )
    assert embed(tmp_path / "x.wav") == [0.0, 0.0]


def test_maest_embedder_rechnet_kurzes_audio_als_ein_fenster(tmp_path: Path):
    seen: list[int] = []

    def window_fn(window):
        seen.append(len(window))
        return [3.0, 4.0]

    embed = make_maest_embedder(
        window_fn_factory=lambda: window_fn,
        load_audio=lambda p: np.ones(16000 * 5, dtype=np.float32),
    )
    assert embed(tmp_path / "x.wav") == pytest.approx([0.6, 0.8])
    assert seen == [16000 * 5]


def test_maest_konstanten():
    assert MAEST_EMBEDDING_LAYER == 7
    assert len(MAEST_HF_REVISION) == 40


def test_split_windows_grenzfaelle():
    sr = 10
    assert len(split_windows(np.arange(30), sr, window_s=3.0)) == 1
    # Genau zwei volle Fenster: Anfang und Ende, ohne Überlappung.
    zwei = split_windows(np.arange(60), sr, window_s=3.0)
    assert [int(w[0]) for w in zwei] == [0, 30]
    assert split_windows(np.arange(100), sr, window_s=3.0, max_windows=0)[0].tolist() == list(range(35, 65))


def _schreibe_wav(path: Path, samples: np.ndarray, sr: int = 22050) -> Path:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(pcm.tobytes())
    return path


def test_mfcc_embedder_leere_datei_ist_value_error(tmp_path: Path):
    embed = make_mfcc_embedder()
    ohne_samples = _schreibe_wav(tmp_path / "null_samples.wav", np.zeros(0))
    with pytest.raises(ValueError):
        embed(ohne_samples)
    leer = tmp_path / "leer.wav"
    leer.write_bytes(b"")
    with pytest.raises(ValueError):
        embed(leer)


def test_mfcc_embedder_stille_ergibt_endliche_werte(tmp_path: Path):
    stille = _schreibe_wav(tmp_path / "stille.wav", np.zeros(22050))
    vector = make_mfcc_embedder()(stille)
    assert len(vector) == 40
    assert all(np.isfinite(vector))


def test_select_embedder_maest_nutzt_modulnamen(monkeypatch):
    marker = lambda path: [1.0]  # noqa: E731
    monkeypatch.setattr(emb, "make_maest_embedder", lambda: marker)
    assert select_embedder("maest") == (marker, emb.MAEST_EMBEDDING_MODEL)


def test_embedding_handler_ohne_client_und_ohne_datei_scheitert(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        handle_embedding_job({"path": str(tmp_path / "fehlt.wav")}, embedder=lambda p: [1.0], model="m")


def test_embedding_handler_ohne_job_id_scheitert_verstaendlich():
    class Client:
        def get_job_audio(self, job_id):  # pragma: no cover - darf nie aufgerufen werden
            raise AssertionError("kein Download ohne job_id")

    with pytest.raises(ValueError, match="job_id"):
        handle_embedding_job({"path": "/server/x.wav"}, Client(), embedder=lambda p: [1.0], model="m")


def test_handler_payload_kein_objekt():
    with pytest.raises(ValueError, match="embedding-Payload"):
        handle_embedding_job([], embedder=lambda p: [1.0], model="m")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="cues-Payload"):
        handle_cues_job([])  # type: ignore[arg-type]


def test_execute_job_meldet_embedder_fehler_als_failed(tmp_path: Path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")

    def boom(path: Path):
        raise RuntimeError("kaputt")

    from sc_digger.cue_infer import build_handler_map

    handlers = build_handler_map(embedder=boom, embedding_model="m")
    outcome = execute_job({"id": 1, "job_type": "embedding", "payload": {"path": str(audio)}}, handlers)
    assert outcome == {"status": "failed", "error": "kaputt"}
    assert audio.is_file()
