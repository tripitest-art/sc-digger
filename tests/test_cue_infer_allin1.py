"""Eigene Tests der all-in-one-Cue-Engine (Issue #424); alles mit Attrappen, ohne torch."""
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

AUDIO = Path("/inbox/track.wav")


class Seg:
    def __init__(self, start, end, label):
        self.start, self.end, self.label = start, end, label


class Res:
    def __init__(self, segments, beats=(), bpm=155.0):
        self.segments = list(segments)
        self.beats = list(beats)
        self.downbeats = []
        self.bpm = bpm


def _infer(result):
    return make_allin1_infer(analyze_fn=lambda paths=None, **kw: result)(AUDIO)


def test_bpm_als_int_wird_float():
    cues = _infer(Res([Seg(0.0, 10.0, "intro")], beats=[1.0], bpm=150))
    assert cues["bpm"] == 150.0 and isinstance(cues["bpm"], float)


@pytest.mark.parametrize("bpm", [None, float("nan"), 0, -3, "abc"])
def test_ungueltige_bpm_wird_none(bpm):
    assert _infer(Res([Seg(0.0, 10.0, "intro")], bpm=bpm))["bpm"] is None


@pytest.mark.parametrize("result", [object(), Res([]), None, []])
def test_ergebnis_ohne_segmente_laesst_den_job_scheitern(result):
    with pytest.raises(RuntimeError, match="Segmente"):
        _infer(result)


def test_ergebnis_als_liste_nutzt_den_ersten_eintrag():
    cues = _infer([Res([Seg(0.0, 10.0, "intro"), Seg(10.0, 50.0, "chorus")], beats=[10.5])])
    assert cues["drop_s"] == 10.0 and cues["first_kick_s"] == 10.5


def test_aufeinanderfolgende_drop_labels_sind_ein_drop():
    segments = [
        Seg(0.0, 16.0, "start"), Seg(16.0, 48.0, "verse"), Seg(48.0, 80.0, "chorus"),
        Seg(80.0, 100.0, "bridge"), Seg(100.0, 140.0, "solo"),
    ]
    cues = _infer(Res(segments, beats=[16.0, 16.5]))
    assert cues["drop_s"] == 16.0
    assert cues["breakdown_s"] == 80.0
    assert cues["drop2_s"] == 100.0  # erst nach dem Break, nicht schon nach der Strophe


def test_unbekanntes_label_wird_drop():
    cues = _infer(Res([Seg(0.0, 8.0, "intro"), Seg(8.0, 40.0, "neuesLabel")], beats=[8.0]))
    assert cues["drop_s"] == 8.0


def test_erster_kick_liegt_nach_dem_intro_nicht_im_kicklosen_intro():
    beats = [0.5 * i for i in range(200)]  # Beat-Tracker schlägt auch im Intro
    cues = _infer(Res([Seg(0.0, 16.2, "intro"), Seg(16.2, 90.0, "chorus")], beats=beats))
    assert cues["first_kick_s"] == 16.5


def test_keine_schlaege_nach_dem_intro_nimmt_das_intro_ende():
    cues = _infer(Res([Seg(0.0, 16.0, "intro"), Seg(16.0, 30.0, "chorus")], beats=[1.0, 2.0]))
    assert cues["first_kick_s"] == 16.0


def test_ohne_schlaege_bleibt_first_kick_null():
    cues = _infer(Res([Seg(0.0, 16.0, "intro"), Seg(16.0, 30.0, "chorus")], beats=[]))
    assert cues["first_kick_s"] == 0.0


def test_ergebnisform_entspricht_dem_vertrag():
    cues = _infer(Res([Seg(0.0, 10.0, "intro"), Seg(10.0, 50.0, "break")], beats=[10.0]))
    assert set(cues) >= {
        "first_kick_s", "drop_s", "drop2_s", "breakdown_s", "breakdown_kind",
        "breakdown_ratio", "cues", "engine", "bpm",
    }
    assert cues["breakdown_kind"] == "groove"
    assert cues["breakdown_ratio"] == 0.8


def test_ein_pfad_als_str_wird_zu_path():
    seen = {}

    def fake(paths=None, **kw):
        seen["paths"] = paths
        return Res([Seg(0.0, 5.0, "intro")])

    make_allin1_infer(analyze_fn=fake)("/inbox/x.wav")  # type: ignore[arg-type]
    assert seen["paths"] == Path("/inbox/x.wav")


def test_fehlende_datei_lokal_ergibt_standardwerte_mit_engine():
    cues = infer_cues_from_audio(Path("/gibt/es/nicht.wav"))
    assert cues["engine"] == "local"
    assert cues["cues"] == []
    assert cues["first_kick_s"] == 0.0


def test_fehlendes_paket_meldet_engine_nicht_verfuegbar(monkeypatch):
    monkeypatch.setitem(sys.modules, "allin1_infer", None)
    with pytest.raises(CueEngineUnavailable):
        select_cue_engine("allin1")


def test_worker_nutzt_bei_verfuegbarem_allin1_die_gpu_inferenz(monkeypatch):
    fake_mod = type(sys)("allin1_infer")
    fake_mod.analyze = lambda paths=None, **kw: Res([Seg(0.0, 5.0, "intro")])
    monkeypatch.setitem(sys.modules, "allin1_infer", fake_mod)
    seen = {}

    def fake_build(client, infer_fn=None, **kwargs):
        seen["infer_fn"] = infer_fn
        return {"cues": lambda payload: None}

    import sc_digger.cue_infer as ci
    monkeypatch.setattr(ci, "build_handler_map", fake_build)
    monkeypatch.setattr(worker, "run_worker_loop", lambda client, **kw: 0)
    assert worker.main(["--api-url", "http://127.0.0.1:9", "--password", "x", "--once",
                        "--embedder", "none"]) == 0
    assert seen["infer_fn"](AUDIO)["engine"] == "allin1"


def test_worker_local_gibt_keine_warnung(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "allin1_infer", None)
    monkeypatch.setattr(worker, "run_worker_loop", lambda client, **kw: 0)
    with caplog.at_level("WARNING"):
        assert worker.main(["--api-url", "http://127.0.0.1:9", "--password", "x", "--once",
                            "--cue-engine", "local", "--embedder", "none"]) == 0
    assert "allin1" not in caplog.text.lower()


def test_build_handler_map_ruft_uebergebene_infer_fn_auf(tmp_path):
    import sc_digger.cue_infer as ci
    audio_file = tmp_path / "track.wav"
    audio_file.write_bytes(b"RIFF dummy")
    called = []

    def fake_infer(p):
        called.append(p)
        return {"engine": "custom", "first_kick_s": 10.0, "cues": []}

    handlers = ci.build_handler_map(client=None, infer_fn=fake_infer)
    res = handlers["cues"]({"path": str(audio_file)})
    assert called == [audio_file]
    assert res["engine"] == "custom"

