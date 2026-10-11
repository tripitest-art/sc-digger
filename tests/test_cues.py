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
