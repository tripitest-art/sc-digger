"""Akzeptanztests: GPU-Cue-Handler für den Workstation-Runner (Issue #227 Teil 2)."""
from pathlib import Path
import pytest

from sc_digger.cue_infer import build_handler_map, extract_cues_from_segments, handle_cues_job
from sc_digger.worker import JobWorkerClient, execute_job, run_worker_loop

ROOT = Path(__file__).resolve().parents[2]


def test_extract_cues_from_segments_maps_all_cue_types():
    segments = [
        {"start": 0.0, "end": 16.0, "label": "intro"},
        {"start": 16.0, "end": 48.0, "label": "drop"},
        {"start": 48.0, "end": 80.0, "label": "breakdown"},
        {"start": 80.0, "end": 128.0, "label": "drop"},
        {"start": 128.0, "end": 160.0, "label": "outro"},
    ]
    beats = [0.0, 4.0, 8.0, 12.0, 16.0, 20.0]

    cues = extract_cues_from_segments(segments, beats=beats, duration=160.0)

    assert cues["first_kick_s"] == 0.0
    assert cues["drop_s"] == 16.0
    assert cues["drop2_s"] == 80.0
    assert cues["breakdown_s"] == 48.0
    assert cues["breakdown_kind"] == "groove"
    assert cues["breakdown_ratio"] == pytest.approx(0.2, abs=0.01)

    cue_names = [c["name"] for c in cues["cues"]]
    assert "First Kick" in cue_names
    assert "Drop" in cue_names
    assert "Drop 2" in cue_names
    assert "Breakdown" in cue_names


def test_handle_cues_job_uses_local_audio_file(tmp_path: Path):
    audio_file = tmp_path / "track.wav"
    audio_file.write_bytes(b"RIFF" + b"\x00" * 40)

    def mock_infer(path: Path):
        assert path == audio_file
        return {
            "first_kick_s": 4.0,
            "drop_s": 32.0,
            "drop2_s": None,
            "breakdown_s": 64.0,
            "breakdown_kind": "silence",
            "breakdown_ratio": 0.15,
            "cues": [{"name": "First Kick", "time": 4.0}],
        }

    payload = {"job_id": 10, "track_id": 1, "path": str(audio_file)}
    res = handle_cues_job(payload, infer_fn=mock_infer)

    assert res["first_kick_s"] == 4.0
    assert res["drop_s"] == 32.0
    assert res["breakdown_kind"] == "silence"


def test_handle_cues_job_downloads_audio_when_file_not_local(tmp_path: Path):
    downloaded_paths = []

    class FakeClient:
        def get_job_audio(self, job_id: int) -> bytes:
            assert job_id == 42
            return b"AUDIO-STREAM-BYTES"

    def mock_infer(path: Path):
        downloaded_paths.append(path)
        assert path.exists()
        assert path.read_bytes() == b"AUDIO-STREAM-BYTES"
        return {"first_kick_s": 2.0, "drop_s": 16.0, "cues": []}

    payload = {"job_id": 42, "track_id": 5, "path": "/server/inbox/remote_track.mp3", "format": "mp3"}
    res = handle_cues_job(payload, client=FakeClient(), infer_fn=mock_infer)

    assert res["first_kick_s"] == 2.0
    # Die temporäre Datei muss im finally-Block gelöscht worden sein
    assert len(downloaded_paths) == 1
    assert not downloaded_paths[0].exists()


def test_worker_loop_processes_cue_job_with_handler_map(tmp_path: Path):
    audio_file = tmp_path / "track.mp3"
    audio_file.write_bytes(b"\x00" * 50)

    class FakeClient:
        def __init__(self):
            self.claimed = False
            self.completed_jobs = {}

        def claim_job(self, job_type=None):
            if not self.claimed:
                self.claimed = True
                return {
                    "id": 100,
                    "job_type": "cues",
                    "payload": {"job_id": 100, "track_id": 9, "path": str(audio_file)},
                }
            return None

        def heartbeat(self, job_id):
            return True

        def complete_job(self, job_id, status, result=None, error=None):
            self.completed_jobs[job_id] = {"status": status, "result": result, "error": error}
            return True

    client = FakeClient()
    handlers = build_handler_map(
        client=client,
        infer_fn=lambda p: {"first_kick_s": 8.0, "drop_s": 32.0, "cues": []},
    )

    processed = run_worker_loop(client, max_jobs=1, handler_map=handlers)

    assert processed == 1
    assert 100 in client.completed_jobs
    assert client.completed_jobs[100]["status"] == "completed"
    assert client.completed_jobs[100]["result"]["first_kick_s"] == 8.0
