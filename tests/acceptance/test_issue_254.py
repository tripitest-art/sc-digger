"""Akzeptanztests: Phrasen-Quantisierung in Cues und Rekordbox-Export."""
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
import pytest

from sc_digger import cues
from sc_digger import rekordbox as rb
from sc_digger import structure as st
from sc_digger.models import Config
from sc_digger.stems import Breakdown, Drop, StemAnalysisResult
from sc_digger.structure import Segment

PATH_MAP = {"/test/inbox": "Z:/Inbox"}


def _audio(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "anoisesrc=d=1:c=pink:r=44100:a=0.2", "-ac", "2", str(path)],
        check=True,
    )
    return path


def _parse(xml_str: str) -> ET.Element:
    return ET.fromstring(xml_str.split("\n", 1)[1])


def test_build_xml_phrase_align_cues():
    # 150 BPM -> Beat = 0.4s. 32 Beats = 12.8s.
    # first_kick = 0.0s.
    # drop = 12.4s (Versatz -0.4s = -1 Beat) -> quantisiert auf 12.8s.
    # breakdown = 25.0s (62.5 Beats -> 64 Beats = 25.6s, Versatz -1.5 Beats) -> quantisiert auf 25.6s.
    track = rb.InboxTrack(
        path=Path("/test/inbox/150/5A/track.wav"),
        title="T",
        artist="A",
        genre="Schranz",
        bpm=150.0,
        key="5A",
        comment="",
        duration_s=300.0,
        size=1000,
        added=datetime(2026, 10, 14, 12, 0),
        first_kick_s=0.0,
        drop_s=12.4,
        breakdown_s=25.0,
        breakdown_kind="groove",
    )

    # Ohne Quantisierung (Standard): exakte Ausgangswerte
    xml_raw = rb.build_xml([track], PATH_MAP, phrase_align_cues=False)
    root_raw = _parse(xml_raw)
    marks_raw = {m.get("Num"): m.get("Start") for m in root_raw.findall("COLLECTION/TRACK/POSITION_MARK")}
    assert marks_raw["1"] == "12.400"
    assert marks_raw["3"] == "25.000"

    # Mit Quantisierung: auf Phrasengitter ausgerichtet
    xml_quant = rb.build_xml([track], PATH_MAP, phrase_align_cues=True)
    root_quant = _parse(xml_quant)
    marks_quant = {m.get("Num"): m.get("Start") for m in root_quant.findall("COLLECTION/TRACK/POSITION_MARK")}
    assert marks_quant["0"] == "0.000"
    assert marks_quant["1"] == "12.800"
    assert marks_quant["3"] == "25.600"


def test_write_stem_cues_mit_phrase_align(tmp_path):
    p = _audio(tmp_path / "track.wav")
    res = StemAnalysisResult(
        breakdowns=[Breakdown(start_s=25.0, end_s=38.0, kind="silence", drum_rms_ratio=0.02)],
        drops=[Drop(start_s=38.0, energy_ratio=0.9)],
        first_kick_s=0.0,
    )
    # 150 BPM: 32 Beats = 12.8s. 3 * 32 = 96 Beats = 38.4s. 38.0s liegt 1 Beat vor 38.4s -> 38.4s.
    # 25.0s liegt 1.5 Beats vor 64 Beats (25.6s) -> 25.6s.
    assert cues.write_stem_cues(p, res, bpm=150.0, phrase_align=True) is True
    assert cues.read_first_kick_tag(p) == pytest.approx(0.0, abs=0.001)
    assert cues.read_drop_tag(p) == pytest.approx(38.4, abs=0.001)
    assert cues.read_breakdown_tag(p) == pytest.approx(25.6, abs=0.001)


def test_find_drop_start_mit_phrase_align():
    segs = [
        Segment("intro", 0.0, 6.4),
        Segment("main", 6.4, 25.0),
        Segment("breakdown", 25.0, 38.0),
        Segment("main", 38.0, 60.0),
    ]
    # Ohne Quantisierung
    assert st.find_drop_start(segs) == pytest.approx(38.0, abs=0.001)

    # Mit Quantisierung (first_kick = 6.4s, 150 BPM):
    # 38.0s - 6.4s = 31.6s. 31.6 / 0.4 = 79 Beats.
    # Nächste 16-Beat-Grenze ist 80 Beats = 32.0s ab first_kick -> 6.4 + 32.0 = 38.4s.
    res = st.find_drop_start(segs, bpm=150.0, first_kick_s=6.4, phrase_align=True)
    assert res == pytest.approx(38.4, abs=0.001)


def test_schema_und_config_cues_phrase_align():
    from sc_digger.schema import FIELDS, validate_config
    field_paths = {f.path for f in FIELDS}
    assert "rekordbox.cues_phrase_align" in field_paths

    cfg = Config.load(Path(__file__).resolve().parents[2] / "config.yaml")
    assert "cues_phrase_align" in cfg.raw.get("rekordbox", {})
    assert validate_config(cfg.raw) == []
