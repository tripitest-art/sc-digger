"""Akzeptanztests: Cue-Analyse auf der Workstation mit all-in-one, lokale Analyse als Rückfall."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sc_digger import cue_infer, worker
from sc_digger.cue_infer import (
    CueEngineUnavailable,
    infer_cues_from_audio,
    make_allin1_infer,
    select_cue_engine,
)
from sc_digger.worker import _build_parser

AUDIO = Path("/inbox/track.wav")


class Segment:
    def __init__(self, start: float, end: float, label: str):
        self.start, self.end, self.label = start, end, label


class Result:
    """Form von allin1_infer.AnalysisResult (getestet: typings.py)."""

    def __init__(self, segments, beats, bpm=155, downbeats=None):
        self.segments = segments
        self.beats = beats
        self.downbeats = downbeats if downbeats is not None else beats[::4]
        self.bpm = bpm


class FakeAnalyze:
    """Ersetzt allin1_infer.analyze; zeichnet Aufrufe auf."""

    def __init__(self, result):
        self.result = result
        self.calls: list[dict] = []

    def __call__(self, paths=None, **kwargs):
        self.calls.append({"paths": paths, **kwargs})
        return self.result


# --- all-in-one-Auswertung -------------------------------------------------------


def test_allin1_segments_werden_auf_unsere_labels_abgebildet():
    result = Result(
        segments=[
            Segment(0.0, 20.0, "intro"),
            Segment(20.0, 95.0, "chorus"),
            Segment(95.0, 120.0, "break"),
            Segment(120.0, 200.0, "chorus"),
            Segment(200.0, 220.0, "outro"),
        ],
        beats=[20.0 + b * 0.5 for b in range(0, 400)],  # erster Schlag nach dem Intro
    )
    infer = make_allin1_infer(analyze_fn=FakeAnalyze(result))
    cues = infer(AUDIO)
    # Intro endet bei 20 s, der erste Schlag danach ist der Kick
    assert cues["first_kick_s"] == 20.0
    assert cues["drop_s"] == 20.0
    assert cues["drop2_s"] == 120.0
    assert cues["breakdown_s"] == 95.0
    assert cues["engine"] == "allin1"


def test_allin1_analysis_errors_reach_the_job():
    def kaputt(paths=None, **kwargs):
        raise RuntimeError("Demucs abgestürzt")

    infer = make_allin1_infer(analyze_fn=kaputt)
    with pytest.raises(RuntimeError, match="Demucs"):
        infer(AUDIO)  # kein stiller Rückfall: der Job soll scheitern


def test_allin1_runs_on_the_gpu_and_keeps_no_byproducts():
    fake = FakeAnalyze(Result(segments=[Segment(0.0, 10.0, "intro")], beats=[0.5]))
    make_allin1_infer(analyze_fn=fake)(AUDIO)
    call = fake.calls[0]
    assert call["paths"] == AUDIO
    assert call["device"] == "cuda"
    assert call["keep_byproducts"] is False
    assert call["model"] == "harmonix-all"
    assert call["multiprocess"] is False  # im Runner keine Kindprozesse


def test_without_allin1_the_engine_reports_a_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "allin1_infer", None)  # Import scheitert wie ohne Paket
    with pytest.raises(CueEngineUnavailable, match="allin1"):
        make_allin1_infer()


def test_select_cue_engine_allin1_and_local(monkeypatch):
    infer, name = select_cue_engine("local")
    assert name == "local" and infer is infer_cues_from_audio

    fake_mod = type(sys)("allin1_infer")
    fake_mod.analyze = FakeAnalyze(Result(segments=[Segment(0.0, 5.0, "intro")], beats=[0.5]))
    monkeypatch.setitem(sys.modules, "allin1_infer", fake_mod)
    infer, name = select_cue_engine("allin1")
    assert name == "allin1"
    assert infer(AUDIO)["engine"] == "allin1"


def test_select_cue_engine_unknown_name_is_rejected():
    with pytest.raises(ValueError, match="cue-engine"):
        select_cue_engine("beat_this")


# --- Rückfall und Verdrahtung ----------------------------------------------------


def test_local_engine_keeps_the_previous_result_shape():
    cues = infer_cues_from_audio(Path("/gibt/es/nicht.wav"))
    assert cues["engine"] == "local"
    assert set(cues) >= {"first_kick_s", "drop_s", "drop2_s", "breakdown_s", "cues", "engine"}


def test_worker_cli_has_cue_engine_option_with_allin1_as_default():
    args = _build_parser().parse_args(["--api-url", "http://x"])
    assert args.cue_engine == "allin1"
    args = _build_parser().parse_args(["--api-url", "http://x", "--cue-engine", "local"])
    assert args.cue_engine == "local"


def test_worker_without_allin1_falls_back_to_local_and_says_so(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "allin1_infer", None)
    seen: dict = {}

    def fake_loop(client, **kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(worker, "run_worker_loop", fake_loop)
    with caplog.at_level("WARNING"):
        assert worker.main(["--api-url", "http://127.0.0.1:9", "--password", "x", "--once"]) == 0
    assert "allin1" in caplog.text.lower()
    assert "cues" in seen["handler_map"]
