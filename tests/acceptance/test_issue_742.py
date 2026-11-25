"""Akzeptanztests: Stem-Auswertung im all-in-one-Cue-Job (Fake-Drop-Korrektur, Breakdown-Art)."""
from __future__ import annotations

import copy
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from sc_digger import cue_infer, worker
from sc_digger.cue_infer import (
    infer_cues_from_audio,
    make_allin1_infer,
    refine_cues_with_stems,
    select_cue_engine,
)
from sc_digger.worker import _build_parser

SR = 22050
DURATION = 60.0
AUDIO = Path("/inbox/track.wav")


def _write_wav(path: Path, data: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(data, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())
    return path


def _bass() -> np.ndarray:
    """60-Hz-Kicks alle 0,5 s von 8 bis 29,5 s und ab 42 s; Kick-Pause 30–42 s."""
    out = np.zeros(int(SR * DURATION))
    n = int(SR * 0.15)
    kick = np.sin(2 * np.pi * 60 * np.arange(n) / SR) * np.hanning(n)
    for t in [*np.arange(8.0, 30.0, 0.5), *np.arange(42.0, DURATION - 0.5, 0.5)]:
        i = int(round(t * SR))
        out[i:i + n] += kick
    return out


def _drums(silent_break: bool) -> np.ndarray:
    data = np.random.default_rng(7).standard_normal(int(SR * DURATION)) * 0.4
    if silent_break:
        data[int(29.6 * SR):int(42.0 * SR)] = 0.0
    return data


def _stems(folder: Path, *, silent_break: bool = True) -> tuple[Path, Path]:
    return (
        _write_wav(folder / "bass.wav", _bass()),
        _write_wav(folder / "drums.wav", _drums(silent_break)),
    )


def _cues(drop_s: float | None = 40.0, breakdown_s: float | None = 30.0) -> dict:
    cues = [{"name": "First Kick", "time": 8.0, "type": "cue"}]
    if drop_s is not None:
        cues.append({"name": "Drop", "time": drop_s, "type": "cue"})
    if breakdown_s is not None:
        cues.append({"name": "Breakdown", "time": breakdown_s, "type": "cue"})
    return {
        "first_kick_s": 8.0,
        "drop_s": drop_s,
        "drop2_s": None,
        "breakdown_s": breakdown_s,
        "breakdown_kind": "groove",
        "breakdown_ratio": 0.2,
        "cues": cues,
        "engine": "allin1",
        "bpm": 150.0,
    }


def _cue_time(result: dict, name: str) -> float | None:
    return next((c["time"] for c in result["cues"] if c["name"] == name), None)


# --- refine_cues_with_stems ---------------------------------------------------------


def test_fake_drop_wird_auf_den_subbass_einsatz_korrigiert(tmp_path):
    bass, drums = _stems(tmp_path)
    original = _cues(drop_s=40.0)
    before = copy.deepcopy(original)

    out = refine_cues_with_stems(original, bass, drums)

    assert out["stem_checked"] is True
    assert out["drop_s"] == pytest.approx(42.0, abs=0.1)
    assert out["drop_offset_s"] == pytest.approx(2.0, abs=0.1)
    assert _cue_time(out, "Drop") == out["drop_s"]
    assert _cue_time(out, "First Kick") == 8.0
    assert _cue_time(out, "Breakdown") == 30.0
    assert original == before  # Eingabe bleibt unverändert


def test_richtiger_drop_bleibt_stehen(tmp_path):
    bass, drums = _stems(tmp_path)
    out = refine_cues_with_stems(_cues(drop_s=42.0), bass, drums)
    assert out["stem_checked"] is True
    assert out["drop_s"] == 42.0
    assert out["drop_offset_s"] == 0.0
    assert _cue_time(out, "Drop") == 42.0


def test_stille_kick_pause_ergibt_silence(tmp_path):
    bass, drums = _stems(tmp_path, silent_break=True)
    out = refine_cues_with_stems(_cues(breakdown_s=30.0), bass, drums)
    assert out["breakdown_kind"] == "silence"


def test_kick_pause_mit_drums_ergibt_groove(tmp_path):
    bass, drums = _stems(tmp_path, silent_break=False)
    original = _cues(breakdown_s=30.0)
    original["breakdown_kind"] = "silence"
    out = refine_cues_with_stems(original, bass, drums)
    assert out["breakdown_kind"] == "groove"


def test_breakdown_ohne_passende_kick_pause_behaelt_die_art(tmp_path):
    bass, drums = _stems(tmp_path, silent_break=True)
    out = refine_cues_with_stems(_cues(breakdown_s=55.0), bass, drums)
    assert out["breakdown_kind"] == "groove"
    assert out["breakdown_s"] == 55.0


def test_ohne_drop_und_breakdown_bleiben_die_cues(tmp_path):
    bass, drums = _stems(tmp_path)
    out = refine_cues_with_stems(_cues(drop_s=None, breakdown_s=None), bass, drums)
    assert out["stem_checked"] is True
    assert out["drop_s"] is None
    assert out["breakdown_kind"] == "groove"
    assert out["drop_offset_s"] == 0.0
    assert [c["name"] for c in out["cues"]] == ["First Kick"]


def test_unlesbare_stems_aendern_nichts(tmp_path):
    original = _cues(drop_s=40.0)
    out = refine_cues_with_stems(original, tmp_path / "fehlt.wav", tmp_path / "fehlt2.wav")
    assert out["stem_checked"] is False
    assert out["drop_offset_s"] == 0.0
    assert {k: v for k, v in out.items() if k not in ("stem_checked", "drop_offset_s")} == original


# --- make_allin1_infer(stems=...) ---------------------------------------------------


class Segment:
    def __init__(self, start: float, end: float, label: str):
        self.start, self.end, self.label = start, end, label


class Result:
    def __init__(self):
        # all-in-one meldet den Drop bei 40 s, der Subbass setzt erst bei 42 s ein.
        self.segments = [Segment(0.0, 40.0, "intro"), Segment(40.0, DURATION, "chorus")]
        self.beats = [40.0 + 0.4 * i for i in range(40)]
        self.downbeats: list[float] = []
        self.bpm = 150


class FakeAnalyze:
    """Ersetzt allin1_infer.analyze und legt die Demucs-Stems wie all-in-one ab."""

    def __init__(self, *, write_stems: bool = True, fail: bool = False):
        self.calls: list[dict] = []
        self.write_stems = write_stems
        self.fail = fail

    def __call__(self, paths=None, **kwargs):
        self.calls.append({"paths": paths, **kwargs})
        if self.write_stems and kwargs.get("demix_dir"):
            _stems(Path(kwargs["demix_dir"]) / "htdemucs" / "track")
        if self.fail:
            raise RuntimeError("Demucs abgestürzt")
        return Result()


def test_allin1_mit_stems_korrigiert_den_drop_und_raeumt_auf():
    fake = FakeAnalyze()
    cues = make_allin1_infer(analyze_fn=fake, stems=True)(AUDIO)

    call = fake.calls[0]
    assert call["keep_byproducts"] is True
    demix, spec = Path(call["demix_dir"]), Path(call["spec_dir"])
    assert demix.parent == spec.parent
    assert not demix.parent.exists()  # Stems und Spektrogramme sind wieder gelöscht

    assert cues["engine"] == "allin1"
    assert cues["stem_checked"] is True
    assert cues["drop_s"] == pytest.approx(42.0, abs=0.1)
    assert _cue_time(cues, "Drop") == cues["drop_s"]


def test_allin1_mit_stems_ohne_stem_dateien_warnt(caplog):
    fake = FakeAnalyze(write_stems=False)
    with caplog.at_level("WARNING"):
        cues = make_allin1_infer(analyze_fn=fake, stems=True)(AUDIO)
    assert cues["stem_checked"] is False
    assert cues["drop_s"] == 40.0
    assert "Stems fehlen" in caplog.text
    assert not Path(fake.calls[0]["demix_dir"]).parent.exists()


def test_allin1_fehler_mit_stems_scheitert_und_raeumt_auf():
    fake = FakeAnalyze(fail=True)
    with pytest.raises(RuntimeError, match="Demucs"):
        make_allin1_infer(analyze_fn=fake, stems=True)(AUDIO)
    assert not Path(fake.calls[0]["demix_dir"]).parent.exists()


def test_allin1_ohne_stems_bleibt_wie_bisher():
    fake = FakeAnalyze()
    cues = make_allin1_infer(analyze_fn=fake)(AUDIO)
    call = fake.calls[0]
    assert call["keep_byproducts"] is False
    assert "demix_dir" not in call and "spec_dir" not in call
    assert "stem_checked" not in cues
    assert cues["drop_s"] == 40.0


# --- Auswahl und Worker-CLI ---------------------------------------------------------


def test_select_cue_engine_reicht_stems_durch(monkeypatch):
    seen: dict = {}

    def fake_make(**kwargs):
        seen.update(kwargs)
        return lambda path: {"engine": "allin1"}

    monkeypatch.setattr(cue_infer, "make_allin1_infer", fake_make)
    _infer, name = select_cue_engine("allin1", stems=True)
    assert name == "allin1" and seen.get("stems") is True

    infer, name = select_cue_engine("local", stems=True)
    assert name == "local" and infer is infer_cues_from_audio


def test_worker_cli_stems_ist_standardmaessig_aus():
    assert _build_parser().parse_args(["--api-url", "http://x"]).stems is False
    assert _build_parser().parse_args(["--api-url", "http://x", "--stems"]).stems is True


def test_worker_main_gibt_stems_an_die_engine_weiter(monkeypatch, caplog):
    seen: dict = {}

    def fake_select(name, **kwargs):
        seen.update(kwargs, name=name)
        return (lambda path: {"engine": name}), name

    monkeypatch.setattr(cue_infer, "select_cue_engine", fake_select)
    monkeypatch.setattr(worker, "run_worker_loop", lambda client, **kw: 0)
    with caplog.at_level("INFO"):
        rc = worker.main([
            "--api-url", "http://127.0.0.1:9", "--password", "x", "--once",
            "--embedder", "none", "--stems",
        ])
    assert rc == 0
    assert seen == {"name": "allin1", "stems": True}
    assert "Stem-Auswertung: an" in caplog.text
