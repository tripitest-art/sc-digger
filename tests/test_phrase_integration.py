"""Tests: Phrasen-Quantisierung (sc_digger.phrase) in Cues, Rekordbox-XML und Strukturanalyse.

Deckt die Integration aus #254 ab: Phantomwerte außerhalb der Toleranz, fehlende
Voraussetzungen (bpm/first_kick), Downbeat-Fallback, Nicht-Mutation der Tracks und die
Steuerung des XML-Exports über die Config-Option `rekordbox.cues_phrase_align`.
Kein Netz, kein echtes Audio (außer einem 1-Sekunden-Rauschen für die Tag-Tests).
"""
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from sc_digger import cues
from sc_digger import rekordbox as rb
from sc_digger import structure as st
from sc_digger.stems import Breakdown, Drop, StemAnalysisResult
from sc_digger.structure import Segment

ROOT = Path(__file__).resolve().parents[1]
PATH_MAP = {"/test/inbox": "Z:/Inbox"}


def _audio(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "anoisesrc=d=1:c=pink:r=44100:a=0.2", "-ac", "2", str(path)],
        check=True,
    )
    return path


def _track(*, bpm: float | None = 150.0, first_kick_s: float | None = 0.0,
           drop_s: float | None = 12.4, breakdown_s: float | None = 25.0) -> rb.InboxTrack:
    return rb.InboxTrack(
        path=Path("/test/inbox/150/5A/track.wav"),
        title="T", artist="A", genre="Schranz", bpm=bpm, key="5A",
        comment="", duration_s=300.0, size=1000,
        added=datetime(2026, 10, 14, 12, 0),
        first_kick_s=first_kick_s, drop_s=drop_s, breakdown_s=breakdown_s,
        breakdown_kind="groove",
    )


def _marks(xml_str: str) -> dict[str, str]:
    root = ET.fromstring(xml_str.split("\n", 1)[1])
    return {m.get("Num"): m.get("Start")
            for m in root.findall("COLLECTION/TRACK/POSITION_MARK")}


# ------------------------------------------------------------------ build_xml
def test_build_xml_standard_bleibt_unquantisiert():
    """Ohne phrase_align_cues bleiben die Cue-Zeiten exakt (Rückwärtskompatibilität)."""
    marks = _marks(rb.build_xml([_track()], PATH_MAP))
    assert marks["1"] == "12.400"
    assert marks["3"] == "25.000"


def test_build_xml_phrase_align_quantisiert_drop_und_breakdown():
    # 150 BPM: Beat 0.4 s. Drop 12.4 s = 31 Beats -> 32 Beats = 12.8 s.
    # Breakdown 25.0 s = 62.5 Beats -> 64 Beats = 25.6 s.
    marks = _marks(rb.build_xml([_track()], PATH_MAP, phrase_align_cues=True))
    assert marks["0"] == "0.000"
    assert marks["1"] == "12.800"
    assert marks["3"] == "25.600"


def test_build_xml_phrase_align_ohne_bpm_unveraendert():
    marks = _marks(rb.build_xml([_track(bpm=None)], PATH_MAP, phrase_align_cues=True))
    assert marks["1"] == "12.400"
    assert marks["3"] == "25.000"


def test_build_xml_phrase_align_ohne_first_kick_unveraendert():
    marks = _marks(rb.build_xml([_track(first_kick_s=None)], PATH_MAP,
                                phrase_align_cues=True))
    assert marks["1"] == "12.400"
    assert marks["3"] == "25.000"


def test_build_xml_phrase_align_mutiert_track_nicht():
    track = _track()
    rb.build_xml([track], PATH_MAP, phrase_align_cues=True)
    assert track.drop_s == 12.4
    assert track.breakdown_s == 25.0


# ------------------------------------------------------------------ write_stem_cues
def test_write_stem_cues_phrase_align_mit_downbeat_fallback(tmp_path):
    # first_kick 0.0, 150 BPM (Beat 0.4 s), Drop 0.8 s = 2 Beats: außerhalb der Toleranz
    # von 1.75 Beats um jeden 4/16/32-Beat-Punkt -> nächster Downbeat 1.6 s.
    p = _audio(tmp_path / "t.wav")
    res = StemAnalysisResult(
        breakdowns=[Breakdown(start_s=0.8, end_s=8.0, kind="silence", drum_rms_ratio=0.02)],
        drops=[],
        first_kick_s=0.0,
    )
    assert cues.write_stem_cues(p, res, bpm=150.0, downbeats=[1.6], phrase_align=True) is True
    assert cues.read_breakdown_tag(p) == pytest.approx(1.6, abs=0.001)


def test_write_stem_cues_phrase_align_ohne_bpm_unveraendert(tmp_path):
    p = _audio(tmp_path / "t.wav")
    res = StemAnalysisResult(
        breakdowns=[Breakdown(start_s=25.0, end_s=38.0, kind="silence", drum_rms_ratio=0.02)],
        drops=[Drop(start_s=38.0, energy_ratio=0.9)],
        first_kick_s=0.0,
    )
    assert cues.write_stem_cues(p, res, phrase_align=True) is True
    assert cues.read_drop_tag(p) == pytest.approx(38.0, abs=0.001)
    assert cues.read_breakdown_tag(p) == pytest.approx(25.0, abs=0.001)


def test_write_stem_cues_phrase_align_ohne_ersten_kick_unveraendert(tmp_path):
    p = _audio(tmp_path / "t.wav")
    res = StemAnalysisResult(
        breakdowns=[Breakdown(start_s=25.0, end_s=38.0, kind="silence", drum_rms_ratio=0.02)],
        drops=[Drop(start_s=38.0, energy_ratio=0.9)],
        first_kick_s=None,
    )
    assert cues.write_stem_cues(p, res, bpm=150.0, phrase_align=True) is True
    assert cues.read_drop_tag(p) == pytest.approx(38.0, abs=0.001)
    assert cues.read_breakdown_tag(p) == pytest.approx(25.0, abs=0.001)


# ------------------------------------------------------------------ find_drop_start
def _segs() -> list[Segment]:
    return [
        Segment("intro", 0.0, 6.4),
        Segment("main", 6.4, 25.0),
        Segment("breakdown", 25.0, 38.0),
        Segment("main", 38.0, 60.0),
    ]


def test_find_drop_start_phrase_align_rastet_auf_sechzehner():
    # (38.0 - 6.4) / 0.4 = 79 Beats -> nächste 16-Beat-Grenze 80 -> 6.4 + 32.0 = 38.4 s.
    assert st.find_drop_start(_segs(), bpm=150.0, first_kick_s=6.4,
                              phrase_align=True) == pytest.approx(38.4, abs=0.001)


def test_find_drop_start_phrase_align_ohne_bpm_unveraendert():
    assert st.find_drop_start(_segs(), first_kick_s=6.4, phrase_align=True) == 38.0


def test_find_drop_start_phrase_align_ohne_ersten_kick_unveraendert():
    assert st.find_drop_start(_segs(), bpm=150.0, phrase_align=True) == 38.0


def test_find_drop_start_phrase_align_downbeat_fallback():
    # Breakdown endet bei 0.8 s = 2 Beats ab first_kick: außerhalb der Toleranz -> Downbeat 1.6 s.
    segs = [Segment("breakdown", 0.0, 0.8), Segment("main", 0.8, 10.0)]
    assert st.find_drop_start(segs, bpm=150.0, first_kick_s=0.0, downbeats=[1.6],
                              phrase_align=True) == pytest.approx(1.6, abs=0.001)


# ------------------------------------------------------------------ write_rekordbox_xml
def _run_xml(tmp_path, monkeypatch, track: rb.InboxTrack, **rb_opts):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    audio = inbox / "[1] t.wav"
    audio.write_bytes(b"\x00")
    monkeypatch.setattr(rb, "scan_inbox", lambda _inbox: [track])
    xml_path = tmp_path / "out.xml"
    cfg = {
        "rekordbox": {
            "xml_enabled": True,
            "xml_path": str(xml_path),
            "path_map": PATH_MAP,
            "cues_enabled": True,
            **rb_opts,
        },
        "download": {"inbox_dir": str(inbox)},
    }
    out = rb.write_rekordbox_xml(cfg)
    assert out == xml_path
    return _marks(xml_path.read_text(encoding="utf-8"))


def test_write_rekordbox_xml_cues_phrase_align_an(tmp_path, monkeypatch):
    # Drop 45.0 s ab first_kick 6.4 s = 96.5 Beats -> 32-Beat-Grenze 96 -> 44.8 s.
    track = _track(first_kick_s=6.4, drop_s=45.0, breakdown_s=25.6)
    marks = _run_xml(tmp_path, monkeypatch, track, cues_phrase_align=True)
    assert marks["1"] == "44.800"
    assert marks["3"] == "25.600"


def test_write_rekordbox_xml_cues_phrase_align_aus(tmp_path, monkeypatch):
    track = _track(first_kick_s=6.4, drop_s=45.0, breakdown_s=25.6)
    marks = _run_xml(tmp_path, monkeypatch, track, cues_phrase_align=False)
    assert marks["1"] == "45.000"
    assert marks["3"] == "25.600"


def test_write_rekordbox_xml_cues_phrase_align_default_ist_aus(tmp_path, monkeypatch):
    """Ohne Config-Schlüssel quantisiert der Export nicht (Code-Standardwert False)."""
    track = _track(first_kick_s=6.4, drop_s=45.0, breakdown_s=25.6)
    marks = _run_xml(tmp_path, monkeypatch, track)
    assert marks["1"] == "45.000"


def test_config_cues_phrase_align_ist_an():
    """Die ausgelieferte config.yaml aktiviert die Cue-Quantisierung."""
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    assert raw["rekordbox"]["cues_phrase_align"] is True
