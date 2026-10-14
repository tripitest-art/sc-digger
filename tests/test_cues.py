"""Eigene Tests für den First-Kick-Cue-Tag (sc_digger.cues)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sc_digger import cues

FORMATS = [".mp3", ".flac", ".m4a", ".wav", ".aiff"]


def _audio(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ["-b:a", "320k"] if path.suffix == ".mp3" else []
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "anoisesrc=d=1:c=pink:r=44100:a=0.2", "-ac", "2", *args, str(path)],
        check=True,
    )
    return path


@pytest.mark.parametrize("ext", FORMATS)
def test_schreiben_und_lesen(tmp_path, ext):
    p = _audio(tmp_path / f"t{ext}")
    assert cues.write_first_kick_tag(p, 6.4) is True
    assert cues.read_first_kick_tag(p) == pytest.approx(6.4, abs=0.0005)


@pytest.mark.parametrize("ext", FORMATS)
def test_ohne_tag_ist_none(tmp_path, ext):
    p = _audio(tmp_path / f"t{ext}")
    assert cues.read_first_kick_tag(p) is None


def test_negative_und_nichtendliche_werte_werden_abgelehnt(tmp_path):
    p = _audio(tmp_path / "t.mp3")
    for bad in (-0.001, float("nan"), float("inf")):
        assert cues.write_first_kick_tag(p, bad) is False
    assert cues.read_first_kick_tag(p) is None


def test_nicht_vorhandene_datei_und_unbekanntes_format(tmp_path):
    assert cues.write_first_kick_tag(tmp_path / "fehlt.mp3", 1.0) is False
    txt = tmp_path / "x.txt"
    txt.write_text("kein audio")
    assert cues.write_first_kick_tag(txt, 1.0) is False
    assert cues.read_first_kick_tag(txt) is None


def test_lesen_robust_bei_kaputter_datei(tmp_path):
    kaputt = tmp_path / "kaputt.flac"
    kaputt.write_bytes(b"kein flac")
    assert cues.read_first_kick_tag(kaputt) is None


def test_null_ist_gueltig(tmp_path):
    p = _audio(tmp_path / "t.mp3")
    assert cues.write_first_kick_tag(p, 0.0) is True
    assert cues.read_first_kick_tag(p) == pytest.approx(0.0, abs=0.0005)


def test_drop_tag_schreiben_und_lesen(tmp_path):
    p = _audio(tmp_path / "t.mp3")
    assert cues.read_drop_tag(p) is None
    assert cues.write_drop_tag(p, 45.123) is True
    assert cues.read_drop_tag(p) == pytest.approx(45.123, abs=0.0005)
    # Überschreiben ersetzt den Wert (kein Doppel).
    assert cues.write_drop_tag(p, 60.0) is True
    assert cues.read_drop_tag(p) == pytest.approx(60.0, abs=0.0005)


@pytest.mark.parametrize("ext", FORMATS)
def test_breakdown_tag_mit_kind_schreiben_und_lesen(tmp_path, ext):
    p = _audio(tmp_path / f"t{ext}")
    assert cues.read_breakdown_tag(p) is None
    assert cues.read_breakdown_kind_tag(p) is None
    assert cues.write_breakdown_tag(p, 30.5, "groove") is True
    assert cues.read_breakdown_tag(p) == pytest.approx(30.5, abs=0.0005)
    assert cues.read_breakdown_kind_tag(p) == "groove"


def test_breakdown_ohne_kind_laesst_kind_tag_leer(tmp_path):
    p = _audio(tmp_path / "t.mp3")
    # Alte Signatur (nur Sekunden) bleibt gültig und schreibt kein Kind.
    assert cues.write_breakdown_tag(p, 12.0) is True
    assert cues.read_breakdown_tag(p) == pytest.approx(12.0, abs=0.0005)
    assert cues.read_breakdown_kind_tag(p) is None


def test_breakdown_kind_leer_oder_whitespace_ist_none(tmp_path):
    p = _audio(tmp_path / "t.mp3")
    assert cues.write_breakdown_tag(p, 5.0, "   ") is True
    assert cues.read_breakdown_kind_tag(p) is None


def test_negative_und_nichtendliche_stem_werte_werden_abgelehnt(tmp_path):
    p = _audio(tmp_path / "t.mp3")
    for bad in (-1.0, float("nan"), float("inf")):
        assert cues.write_drop_tag(p, bad) is False
        assert cues.write_breakdown_tag(p, bad, "silence") is False
    assert cues.read_drop_tag(p) is None
    assert cues.read_breakdown_tag(p) is None


def test_stem_tags_fehlen_und_datei_unbekannt(tmp_path):
    assert cues.write_drop_tag(tmp_path / "fehlt.mp3", 1.0) is False
    assert cues.write_breakdown_tag(tmp_path / "fehlt.mp3", 1.0, "groove") is False
    assert cues.read_drop_tag(tmp_path / "fehlt.flac") is None
    assert cues.read_breakdown_tag(tmp_path / "fehlt.flac") is None
    assert cues.read_breakdown_kind_tag(tmp_path / "fehlt.flac") is None


def test_write_stem_cues_schreibt_alle_vorhandenen_cues(tmp_path):
    from sc_digger.stems import Breakdown, Drop, StemAnalysisResult

    p = _audio(tmp_path / "t.mp3")
    res = StemAnalysisResult(
        breakdowns=[Breakdown(start_s=25.0, end_s=33.0, kind="silence", drum_rms_ratio=0.05)],
        drops=[Drop(start_s=33.0, energy_ratio=0.88)],
        first_kick_s=0.5,
    )
    assert cues.write_stem_cues(p, res) is True
    assert cues.read_first_kick_tag(p) == pytest.approx(0.5, abs=0.0005)
    assert cues.read_drop_tag(p) == pytest.approx(33.0, abs=0.0005)
    assert cues.read_breakdown_tag(p) == pytest.approx(25.0, abs=0.0005)
    assert cues.read_breakdown_kind_tag(p) == "silence"


def test_write_stem_cues_nutzt_den_ersten_drop_und_breakdown(tmp_path):
    from sc_digger.stems import Breakdown, Drop, StemAnalysisResult

    p = _audio(tmp_path / "t.mp3")
    res = StemAnalysisResult(
        breakdowns=[
            Breakdown(start_s=25.0, end_s=33.0, kind="groove", drum_rms_ratio=0.3),
            Breakdown(start_s=90.0, end_s=98.0, kind="silence", drum_rms_ratio=0.01),
        ],
        drops=[
            Drop(start_s=33.0, energy_ratio=0.9),
            Drop(start_s=98.0, energy_ratio=0.8),
        ],
        first_kick_s=None,
    )
    assert cues.write_stem_cues(p, res) is True
    assert cues.read_first_kick_tag(p) is None
    assert cues.read_drop_tag(p) == pytest.approx(33.0, abs=0.0005)
    assert cues.read_breakdown_tag(p) == pytest.approx(25.0, abs=0.0005)
    assert cues.read_breakdown_kind_tag(p) == "groove"


def test_write_stem_cues_ohne_werte_ist_false_und_schreibt_nichts(tmp_path):
    from sc_digger.stems import StemAnalysisResult

    p = _audio(tmp_path / "t.mp3")
    res = StemAnalysisResult(breakdowns=[], drops=[], first_kick_s=None)
    assert cues.write_stem_cues(p, res) is False
    assert cues.read_first_kick_tag(p) is None
    assert cues.read_drop_tag(p) is None
    assert cues.read_breakdown_tag(p) is None
