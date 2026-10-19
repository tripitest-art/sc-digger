"""Tests für die Cue-Inferenz des GPU-Workers (Issue #354)."""
from __future__ import annotations

from pathlib import Path

import pytest
import requests

from sc_digger import cue_infer
from sc_digger.cue_infer import (
    build_handler_map,
    extract_cues_from_segments,
    handle_cues_job,
    infer_cues_from_audio,
)
from sc_digger.structure import Segment
from sc_digger.worker import execute_job


# --- extract_cues_from_segments ---------------------------------------------------


def test_empty_segments_give_default_values():
    result = extract_cues_from_segments([])

    assert result == {
        "first_kick_s": 0.0,
        "drop_s": None,
        "drop2_s": None,
        "breakdown_s": None,
        "breakdown_kind": "groove",
        "breakdown_ratio": 0.0,
        "cues": [],
    }


def test_invalid_input_never_raises():
    assert extract_cues_from_segments(None)["cues"] == []  # type: ignore[arg-type]
    assert extract_cues_from_segments("kaputt")["cues"] == []  # type: ignore[arg-type]


def test_without_beats_first_kick_is_zero():
    segments = [{"start": 4.0, "end": 10.0, "label": "intro"}]

    result = extract_cues_from_segments(segments, beats=None, duration=10.0)

    assert result["first_kick_s"] == 0.0


def test_first_kick_is_first_beat_at_or_after_non_silence():
    segments = [
        {"start": 0.0, "end": 2.0, "label": "silence"},
        {"start": 2.0, "end": 6.0, "label": "intro"},
    ]

    result = extract_cues_from_segments(segments, beats=[0.5, 1.5, 2.5, 3.5], duration=6.0)

    assert result["first_kick_s"] == 2.5


def test_chorus_counts_as_drop_and_bridge_as_breakdown():
    segments = [
        {"start": 0.0, "end": 10.0, "label": "chorus"},
        {"start": 10.0, "end": 20.0, "label": "bridge"},
    ]

    result = extract_cues_from_segments(segments, beats=[0.0], duration=20.0)

    assert result["drop_s"] == 0.0
    assert result["drop2_s"] is None
    assert result["breakdown_s"] == 10.0
    assert [cue["name"] for cue in result["cues"]] == [
        "First Kick",
        "Drop",
        "Breakdown",
    ]
    assert all(cue["type"] == "cue" for cue in result["cues"])


def test_missing_drop_and_breakdown_are_none():
    result = extract_cues_from_segments(
        [{"start": 0.0, "end": 10.0, "label": "intro"}], beats=[0.0], duration=10.0
    )

    assert result["drop_s"] is None
    assert result["breakdown_s"] is None
    assert [cue["name"] for cue in result["cues"]] == ["First Kick"]


def test_breakdown_kind_silence_wins():
    segments = [
        {"start": 0.0, "end": 5.0, "label": "silence"},
        {"start": 5.0, "end": 10.0, "label": "breakdown"},
    ]

    result = extract_cues_from_segments(segments, beats=[0.0], duration=10.0)

    assert result["breakdown_kind"] == "silence"


def test_invalid_segment_entries_are_ignored():
    segments = [
        None,
        "kaputt",
        {"start": "keine-zahl", "label": "drop"},
        {"start": 1.0, "end": 2.0, "label": "drop"},
    ]

    result = extract_cues_from_segments(segments, beats=[1.0], duration=2.0)  # type: ignore[list-item]

    assert result["drop_s"] == 1.0
    assert result["first_kick_s"] == 1.0


# --- handle_cues_job ---------------------------------------------------------------


def test_handle_prefers_local_path(tmp_path: Path):
    local = tmp_path / "track.mp3"
    local.write_bytes(b"lokal")
    seen: list[Path] = []

    result = handle_cues_job(
        {"local_path": str(local), "path": "/server/weg.mp3"},
        infer_fn=lambda path: seen.append(path) or {"first_kick_s": 1.0},
    )

    assert result == {"first_kick_s": 1.0}
    assert seen == [local]


def test_handle_without_local_file_and_client_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        handle_cues_job({"path": str(tmp_path / "fehlt.mp3")})


def test_handle_download_failure_is_raised():
    class BrokenClient:
        def get_job_audio(self, job_id: int) -> bytes:
            raise requests.ConnectionError("Server weg")

    with pytest.raises(requests.ConnectionError):
        handle_cues_job(
            {"job_id": 7, "path": "/server/weg.mp3"},
            client=BrokenClient(),
            infer_fn=lambda path: {},
        )


def test_handle_requires_job_id_for_download():
    class FakeClient:
        def get_job_audio(self, job_id: int) -> bytes:  # pragma: no cover - Guard
            raise AssertionError("ohne job_id darf nicht geladen werden")

    with pytest.raises(ValueError, match="job_id"):
        handle_cues_job(
            {"path": "/server/weg.mp3"}, client=FakeClient(), infer_fn=lambda path: {}
        )


def test_temporary_file_is_deleted_when_inference_raises():
    seen: list[Path] = []

    class FakeClient:
        def get_job_audio(self, job_id: int) -> bytes:
            assert job_id == 5
            return b"AUDIO"

    def broken(path: Path):
        seen.append(path)
        assert path.read_bytes() == b"AUDIO"
        raise RuntimeError("Inferenz kaputt")

    with pytest.raises(RuntimeError):
        handle_cues_job(
            {"job_id": 5, "path": "/server/track.mp3", "format": "mp3"},
            client=FakeClient(),
            infer_fn=broken,
        )

    assert len(seen) == 1
    assert not seen[0].exists()


def test_handle_uses_default_inference_when_infer_fn_missing(tmp_path: Path, monkeypatch):
    audio = tmp_path / "track.wav"
    audio.write_bytes(b"x")
    monkeypatch.setattr(cue_infer, "infer_cues_from_audio", lambda path: {"first_kick_s": 3.0})

    assert handle_cues_job({"path": str(audio)})["first_kick_s"] == 3.0


# --- build_handler_map / execute_job -----------------------------------------------


def test_build_handler_map_registers_cues_handler():
    assert set(build_handler_map()) == {"cues"}


def test_handler_crash_becomes_failed_result(tmp_path: Path):
    audio = tmp_path / "track.wav"
    audio.write_bytes(b"x")
    handlers = build_handler_map(
        infer_fn=lambda path: (_ for _ in ()).throw(RuntimeError("GPU OOM"))
    )

    outcome = execute_job(
        {"id": 1, "job_type": "cues", "payload": {"job_id": 1, "path": str(audio)}},
        handlers,
    )

    assert outcome["status"] == "failed"
    assert "GPU OOM" in outcome["error"]


# --- infer_cues_from_audio ----------------------------------------------------------


def test_infer_cues_from_audio_maps_structure_segments(monkeypatch):
    monkeypatch.setattr(
        cue_infer,
        "analyze_structure",
        lambda path: [
            Segment("intro", 0.0, 8.0),
            Segment("main", 8.0, 40.0),
            Segment("breakdown", 40.0, 56.0),
            Segment("main", 56.0, 96.0),
        ],
    )
    monkeypatch.setattr(cue_infer, "find_first_kick", lambda path: 8.0)

    result = infer_cues_from_audio(Path("egal.wav"))

    assert result["first_kick_s"] == 8.0
    assert result["drop_s"] == 8.0
    assert result["drop2_s"] == 56.0
    assert result["breakdown_s"] == 40.0
    assert result["breakdown_kind"] == "groove"
    assert result["breakdown_ratio"] == round(16.0 / 96.0, 3)
