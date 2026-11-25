"""Eigene Tests zur Stem-Auswertung im all-in-one-Cue-Job (Issue #742).

Die Stem-Analyse wird hier per Attrappe ersetzt: Geprüft wird, wie ``refine_cues_with_stems``
ihre Ergebnisse übernimmt, nicht die Signalverarbeitung selbst (die prüft ``test_stems.py``).
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from sc_digger import cue_infer, worker
from sc_digger.cue_infer import (
    BREAKDOWN_MATCH_TOL_S,
    make_allin1_infer,
    refine_cues_with_stems,
)
from sc_digger.stems import Breakdown, Drop, StemAnalysisResult

BASS = Path("/tmp/stems/bass.wav")
DRUMS = Path("/tmp/stems/drums.wav")


def _cues(**overrides) -> dict:
    base = {
        "first_kick_s": 8.0,
        "drop_s": 40.0,
        "drop2_s": None,
        "breakdown_s": 30.0,
        "breakdown_kind": "groove",
        "breakdown_ratio": 0.2,
        "engine": "allin1",
        "bpm": 150.0,
    }
    base.update(overrides)
    base["cues"] = cue_infer._build_cues(
        base["first_kick_s"], base["drop_s"], base["drop2_s"], base["breakdown_s"]
    )
    return base


class FakeStems:
    """Ersetzt analyze_stems und find_subbass_drop und merkt sich die Aufrufe."""

    def __init__(self, *, breakdowns=(), drop: Drop | None = None, first_kick_s=8.0):
        self.breakdowns = list(breakdowns)
        self.drop = drop
        self.first_kick_s = first_kick_s
        self.analyze_calls: list[dict] = []
        self.drop_calls: list[dict] = []

    def analyze_stems(self, bass_path, drums_path, **kwargs):
        self.analyze_calls.append({"bass": bass_path, "drums": drums_path, **kwargs})
        return StemAnalysisResult(
            breakdowns=self.breakdowns, drops=[], first_kick_s=self.first_kick_s
        )

    def find_subbass_drop(self, bass_path, search_start_s, **kwargs):
        self.drop_calls.append({"bass": bass_path, "start": search_start_s, **kwargs})
        return self.drop

    def install(self, monkeypatch):
        monkeypatch.setattr(cue_infer.stem_analysis, "analyze_stems", self.analyze_stems)
        monkeypatch.setattr(cue_infer.stem_analysis, "find_subbass_drop", self.find_subbass_drop)
        return self


def _names(result: dict) -> list[str]:
    return [c["name"] for c in result["cues"]]


def test_drop2_bleibt_unveraendert_und_in_der_liste(monkeypatch):
    fake = FakeStems(
        drop=Drop(start_s=42.0, energy_ratio=0.9, is_fake_drop_corrected=True, offset_s=2.0)
    ).install(monkeypatch)
    original = _cues(drop2_s=120.0)

    out = refine_cues_with_stems(original, BASS, DRUMS)

    assert out["drop_s"] == 42.0
    assert out["drop_offset_s"] == 2.0
    assert out["drop2_s"] == 120.0
    assert _names(out) == ["First Kick", "Drop", "Drop 2", "Breakdown"]
    assert next(c["time"] for c in out["cues"] if c["name"] == "Drop 2") == 120.0
    # drop2_s wird nicht verifiziert: nur ein Drop-Check, für den ersten Drop.
    assert [c["start"] for c in fake.drop_calls] == [40.0]


def test_kick_pause_genau_an_der_toleranzgrenze_zaehlt(monkeypatch):
    assert BREAKDOWN_MATCH_TOL_S == 16.0
    pause = Breakdown(start_s=30.0 + BREAKDOWN_MATCH_TOL_S, end_s=60.0, kind="silence",
                      drum_rms_ratio=0.0)
    FakeStems(breakdowns=[pause]).install(monkeypatch)
    out = refine_cues_with_stems(_cues(breakdown_s=30.0), BASS, DRUMS)
    assert out["breakdown_kind"] == "silence"
    assert out["breakdown_s"] == 30.0


def test_kick_pause_knapp_hinter_der_toleranzgrenze_zaehlt_nicht(monkeypatch):
    pause = Breakdown(start_s=30.0 + BREAKDOWN_MATCH_TOL_S + 0.01, end_s=60.0,
                      kind="silence", drum_rms_ratio=0.0)
    FakeStems(breakdowns=[pause]).install(monkeypatch)
    out = refine_cues_with_stems(_cues(breakdown_s=30.0), BASS, DRUMS)
    assert out["breakdown_kind"] == "groove"
    assert out["stem_checked"] is True


def test_naechste_kick_pause_gewinnt(monkeypatch):
    FakeStems(breakdowns=[
        Breakdown(start_s=20.0, end_s=26.0, kind="silence", drum_rms_ratio=0.0),
        Breakdown(start_s=33.0, end_s=40.0, kind="groove", drum_rms_ratio=0.5),
    ]).install(monkeypatch)
    out = refine_cues_with_stems(_cues(breakdown_s=30.0, breakdown_kind="silence"), BASS, DRUMS)
    assert out["breakdown_kind"] == "groove"


def test_downbeats_und_bpm_werden_weitergereicht(monkeypatch):
    fake = FakeStems(drop=Drop(start_s=40.0, energy_ratio=1.0)).install(monkeypatch)
    downbeats = [8.0, 9.6, 40.0, 41.6]

    out = refine_cues_with_stems(_cues(), BASS, DRUMS, downbeats=downbeats, bpm=150.0)

    assert fake.analyze_calls == [
        {"bass": BASS, "drums": DRUMS, "downbeats": downbeats, "bpm": 150.0}
    ]
    assert fake.drop_calls == [
        {"bass": BASS, "start": 40.0, "downbeats": downbeats, "bpm": 150.0}
    ]
    # Bestätigter Drop (nicht korrigiert): gemeldeter Wert bleibt.
    assert out["drop_s"] == 40.0 and out["drop_offset_s"] == 0.0


def test_ohne_drop_kein_drop_check(monkeypatch):
    fake = FakeStems().install(monkeypatch)
    out = refine_cues_with_stems(_cues(drop_s=None), BASS, DRUMS)
    assert fake.drop_calls == []
    assert out["drop_s"] is None
    assert _names(out) == ["First Kick", "Breakdown"]


def test_leere_cue_liste_bleibt_leer(monkeypatch):
    FakeStems(
        drop=Drop(start_s=42.0, energy_ratio=0.9, is_fake_drop_corrected=True, offset_s=2.0)
    ).install(monkeypatch)
    original = _cues()
    original["cues"] = []
    out = refine_cues_with_stems(original, BASS, DRUMS)
    assert out["cues"] == []
    assert out["drop_s"] == 42.0


def test_ausnahme_in_der_stem_analyse_warnt_und_aendert_nichts(monkeypatch, caplog):
    def boom(*_args, **_kwargs):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(cue_infer.stem_analysis, "analyze_stems", boom)
    original = _cues()
    before = copy.deepcopy(original)
    with caplog.at_level("WARNING"):
        out = refine_cues_with_stems(original, BASS, DRUMS)
    assert "Stem-Auswertung fehlgeschlagen" in caplog.text
    assert out["stem_checked"] is False and out["drop_offset_s"] == 0.0
    assert {k: v for k, v in out.items() if k not in ("stem_checked", "drop_offset_s")} == before
    assert original == before


class _Segment:
    def __init__(self, start, end, label):
        self.start, self.end, self.label = start, end, label


class _Result:
    segments = [_Segment(0.0, 40.0, "intro"), _Segment(40.0, 60.0, "chorus")]
    beats = [40.0, 40.4]
    downbeats = [40.0, "kaputt", 41.6]
    bpm = 150


def test_allin1_reicht_downbeats_aus_dem_ergebnis_weiter(monkeypatch):
    seen: dict = {}

    def fake_analyze(paths=None, **kwargs):
        stem_dir = Path(kwargs["demix_dir"]) / "htdemucs" / "track"
        stem_dir.mkdir(parents=True)
        (stem_dir / "bass.wav").write_bytes(b"")
        (stem_dir / "drums.wav").write_bytes(b"")
        return _Result()

    def fake_refine(cues, bass, drums, *, downbeats=None, bpm=None):
        seen.update(bass=bass.name, drums=drums.name, downbeats=downbeats, bpm=bpm)
        return {**cues, "stem_checked": True, "drop_offset_s": 0.0}

    monkeypatch.setattr(cue_infer, "refine_cues_with_stems", fake_refine)
    out = make_allin1_infer(analyze_fn=fake_analyze, stems=True)(Path("/inbox/x.wav"))
    assert seen == {"bass": "bass.wav", "drums": "drums.wav",
                    "downbeats": [40.0, 41.6], "bpm": 150.0}
    assert out["stem_checked"] is True


def test_worker_meldet_stems_aus_beim_rueckfall_auf_local(monkeypatch, caplog):
    calls: list = []

    def fake_select(name, **kwargs):
        calls.append((name, kwargs))
        if name == "allin1":
            raise cue_infer.CueEngineUnavailable("fehlt")
        return (lambda path: {"engine": name}), name

    monkeypatch.setattr(cue_infer, "select_cue_engine", fake_select)
    monkeypatch.setattr(worker, "run_worker_loop", lambda client, **kw: 0)
    with caplog.at_level("INFO"):
        rc = worker.main([
            "--api-url", "http://127.0.0.1:9", "--password", "x", "--once",
            "--embedder", "none", "--stems",
        ])
    assert rc == 0
    assert calls == [("allin1", {"stems": True}), ("local", {})]
    assert "Stem-Auswertung: aus" in caplog.text


@pytest.mark.parametrize("argv_extra, expected", [([], "aus"), (["--stems"], "an")])
def test_worker_log_zeile_stem_auswertung(monkeypatch, caplog, argv_extra, expected):
    monkeypatch.setattr(
        cue_infer, "select_cue_engine", lambda name, **kw: ((lambda path: {}), name)
    )
    monkeypatch.setattr(worker, "run_worker_loop", lambda client, **kw: 0)
    with caplog.at_level("INFO"):
        worker.main([
            "--api-url", "http://127.0.0.1:9", "--password", "x", "--once",
            "--embedder", "none", *argv_extra,
        ])
    assert f"Stem-Auswertung: {expected}" in caplog.text
