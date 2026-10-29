"""Akzeptanztests: Worker rechnet Embedding-Jobs (MAEST auf der Workstation, MFCC als Fallback)."""
from __future__ import annotations

import importlib.util
import math
import subprocess
from pathlib import Path

import numpy as np
import pytest

from sc_digger import embeddings as emb
from sc_digger import worker
from sc_digger.cue_infer import build_handler_map
from sc_digger.embeddings import (
    MAEST_EMBEDDING_MODEL,
    MFCC_EMBEDDING_MODEL,
    EmbedderUnavailable,
    make_maest_embedder,
    make_mfcc_embedder,
    select_embedder,
    split_windows,
)
from sc_digger.worker import run_worker_loop


def _make_wav(path: Path, seconds: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", f"anoisesrc=d={seconds}:c=pink:r=44100:a=0.2:s=1",
            "-ac", "1", str(path),
        ],
        check=True,
    )
    return path


class FakeClient:
    """Liefert eine Job-Queue mit festen Jobs und Audio-Bytes; merkt sich alle Aufrufe."""

    def __init__(self, jobs: list[dict], audio: bytes = b"") -> None:
        self.jobs = list(jobs)
        self.audio = audio
        self.audio_calls: list[int] = []
        self.claims: list[str | None] = []
        self.completed: dict[int, dict] = {}

    def claim_job(self, job_type: str | None = None) -> dict | None:
        self.claims.append(job_type)
        return self.jobs.pop(0) if self.jobs else None

    def heartbeat(self, job_id: int) -> bool:
        return True

    def complete_job(self, job_id, status, result=None, error=None) -> bool:
        self.completed[job_id] = {"status": status, "result": result, "error": error}
        return True

    def get_job_audio(self, job_id: int) -> bytes:
        self.audio_calls.append(job_id)
        return self.audio


def test_embedding_handler_nur_mit_embedder():
    assert set(build_handler_map()) == {"cues"}
    assert set(build_handler_map(embedder=lambda p: [1.0], embedding_model="m")) == {"cues", "embedding"}
    with pytest.raises(ValueError):
        build_handler_map(embedder=lambda p: [1.0])


def test_lokale_datei_wird_gerechnet_und_nicht_geloescht(tmp_path: Path):
    audio = _make_wav(tmp_path / "track.wav")
    seen: list[Path] = []

    def embedder(path: Path) -> list[float]:
        seen.append(path)
        return [1, 2.5]

    handler = build_handler_map(embedder=embedder, embedding_model="test-modell")["embedding"]
    assert handler({"path": str(audio)}) == {"model": "test-modell", "vector": [1.0, 2.5]}
    assert seen == [audio]
    assert audio.is_file()


def test_worker_laedt_audio_ueber_job_id_und_raeumt_auf(tmp_path: Path):
    wav = _make_wav(tmp_path / "quelle.wav").read_bytes()
    seen: list[Path] = []

    def embedder(path: Path) -> list[float]:
        assert path.is_file()
        assert path.read_bytes() == wav
        seen.append(path)
        return [0.5, 0.25]

    job = {"id": 42, "job_type": "embedding",
           "payload": {"track_id": 7, "path": "/server/gibt/es/nicht.wav", "format": "wav"}}
    client = FakeClient([job], audio=wav)
    handlers = build_handler_map(client, embedder=embedder, embedding_model="test-modell")

    assert run_worker_loop(client, poll_interval=0, max_jobs=1, handler_map=handlers) == 1
    assert client.audio_calls == [42]
    assert client.completed[42] == {
        "status": "completed", "result": {"model": "test-modell", "vector": [0.5, 0.25]}, "error": None,
    }
    assert len(seen) == 1 and seen[0].suffix == ".wav"
    assert not seen[0].exists()


def test_temp_datei_wird_auch_bei_fehler_geloescht(tmp_path: Path):
    seen: list[Path] = []

    def boom(path: Path) -> list[float]:
        seen.append(path)
        raise RuntimeError("GPU OOM")

    job = {"id": 5, "job_type": "embedding", "payload": {"path": "/server/x.mp3", "format": "mp3"}}
    client = FakeClient([job], audio=b"\x00" * 64)
    handlers = build_handler_map(client, embedder=boom, embedding_model="m")

    assert run_worker_loop(client, poll_interval=0, max_jobs=1, handler_map=handlers) == 1
    assert client.completed[5]["status"] == "failed"
    assert "GPU OOM" in client.completed[5]["error"]
    assert len(seen) == 1 and not seen[0].exists()


def test_cue_job_ohne_job_id_im_payload_laedt_audio(tmp_path: Path):
    job = {"id": 9, "job_type": "cues", "payload": {"track_id": 3, "path": "/server/y.wav", "format": "wav"}}
    client = FakeClient([job], audio=b"RIFF")
    handlers = build_handler_map(client, infer_fn=lambda path: {"first_kick_s": 1.0})

    assert run_worker_loop(client, poll_interval=0, max_jobs=1, handler_map=handlers) == 1
    assert client.audio_calls == [9]
    assert client.completed[9]["status"] == "completed"
    assert client.completed[9]["result"] == {"first_kick_s": 1.0}


def test_mfcc_embedder_ist_deterministisch(tmp_path: Path):
    audio = _make_wav(tmp_path / "a.wav")
    embed = make_mfcc_embedder()
    first = embed(audio)
    assert len(first) == 40
    assert all(isinstance(x, float) and math.isfinite(x) for x in first)
    assert embed(audio) == first
    assert len(make_mfcc_embedder(n_mfcc=13)(audio)) == 26


def test_split_windows():
    sr = 10
    assert split_windows(np.zeros(0), sr, window_s=3.0) == []
    short = split_windows(np.arange(20), sr, window_s=3.0)
    assert len(short) == 1 and len(short[0]) == 20
    mid = split_windows(np.arange(50), sr, window_s=3.0, max_windows=1)
    assert list(mid[0]) == list(range(10, 40))
    many = split_windows(np.arange(100), sr, window_s=3.0, max_windows=8)
    assert len(many) == 3
    assert all(len(w) == 30 for w in many)
    assert many[0][0] == 0 and many[-1][-1] == 99
    capped = split_windows(np.arange(1000), sr, window_s=3.0, max_windows=4)
    assert len(capped) == 4 and capped[-1][-1] == 999


def test_maest_embedder_mittelt_fenster_und_laedt_modell_einmal(tmp_path: Path):
    loads: list[int] = []

    def factory():
        loads.append(1)
        return lambda window: [float(window[0] > 0), 1.0, 0.0]

    samples = np.concatenate([np.zeros(16000 * 30), np.ones(16000 * 30)]).astype(np.float32)
    embed = make_maest_embedder(window_fn_factory=factory, load_audio=lambda p: samples)
    assert loads == []
    vec = embed(tmp_path / "egal.wav")
    expected = np.array([0.5, 1.0, 0.0]) / np.linalg.norm([0.5, 1.0, 0.0])
    assert np.allclose(vec, expected)
    assert math.isclose(float(np.linalg.norm(vec)), 1.0, rel_tol=1e-9)
    embed(tmp_path / "egal.wav")
    assert loads == [1]


def test_maest_ohne_torch_meldet_embedder_unavailable(monkeypatch):
    real = importlib.util.find_spec

    def fake_find_spec(name, *args, **kwargs):
        if name in ("torch", "transformers"):
            return None
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    with pytest.raises(EmbedderUnavailable):
        make_maest_embedder()
    with pytest.raises(EmbedderUnavailable):
        select_embedder("maest")


def test_select_embedder():
    assert select_embedder("none") is None
    embedder, model = select_embedder("mfcc")
    assert model == MFCC_EMBEDDING_MODEL == "mfcc-stat-v1"
    assert callable(embedder)
    assert MAEST_EMBEDDING_MODEL == "discogs-maest-30s-pw-129e"
    with pytest.raises(ValueError):
        select_embedder("clap")


def _run_main(monkeypatch, argv: list[str]) -> dict:
    captured: dict = {}

    def fake_loop(client, job_type=None, poll_interval=5.0, max_jobs=None, handler_map=None, stop_event=None):
        captured["job_type"] = job_type
        captured["handlers"] = set(handler_map or {})
        return 0

    monkeypatch.setattr(worker, "run_worker_loop", fake_loop)
    assert worker.main(["--api-url", "http://127.0.0.1:9", "--password", "x", *argv]) == 0
    return captured


def test_worker_cli_waehlt_embedder(monkeypatch):
    assert worker._build_parser().parse_args(["--api-url", "http://x"]).embedder == "maest"

    mfcc = _run_main(monkeypatch, ["--embedder", "mfcc"])
    assert mfcc == {"job_type": None, "handlers": {"cues", "embedding"}}

    none = _run_main(monkeypatch, ["--embedder", "none"])
    assert none == {"job_type": "cues", "handlers": {"cues"}}

    explicit = _run_main(monkeypatch, ["--embedder", "none", "--job-type", "cues"])
    assert explicit == {"job_type": "cues", "handlers": {"cues"}}


def test_worker_ohne_maest_pakete_rechnet_nur_cues(monkeypatch):
    def unavailable():
        raise EmbedderUnavailable("torch fehlt")

    monkeypatch.setattr(emb, "make_maest_embedder", unavailable)
    assert _run_main(monkeypatch, []) == {"job_type": "cues", "handlers": {"cues"}}
