import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
import subprocess
import pytest

from sc_digger import cues
from sc_digger import rekordbox as rb
from sc_digger.stems import Breakdown, Drop, StemAnalysisResult

PATH_MAP = {"/test/inbox": "Z:/Inbox"}


def _audio(path: Path) -> Path:
    """Erzeugt eine kurze Testdatei via ffmpeg."""
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ["-b:a", "320k"] if path.suffix == ".mp3" else []
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "anoisesrc=d=1:c=pink:r=44100:a=0.2", "-ac", "2", *args, str(path)],
        check=True,
    )
    return path


def _parse(xml_str: str) -> ET.Element:
    return ET.fromstring(xml_str.split("\n", 1)[1])


def test_tag_konstanten():
    assert cues.DROP_TAG == "SCDIGGER_DROP"
    assert cues.BREAKDOWN_TAG == "SCDIGGER_BREAKDOWN"
    assert cues.BREAKDOWN_KIND_TAG == "SCDIGGER_BREAKDOWN_KIND"


@pytest.mark.parametrize("ext", [".mp3", ".flac", ".wav", ".aiff", ".m4a"])
def test_drop_und_breakdown_tags_schreiben_und_lesen(tmp_path, ext):
    p = _audio(tmp_path / f"track{ext}")

    assert cues.read_drop_tag(p) is None
    assert cues.read_breakdown_tag(p) is None
    assert cues.read_breakdown_kind_tag(p) is None

    assert cues.write_drop_tag(p, 45.123) is True
    assert cues.read_drop_tag(p) == pytest.approx(45.123, abs=0.001)

    assert cues.write_breakdown_tag(p, 30.5, "groove") is True
    assert cues.read_breakdown_tag(p) == pytest.approx(30.5, abs=0.001)
    assert cues.read_breakdown_kind_tag(p) == "groove"


def test_write_stem_cues_bulk(tmp_path):
    p = _audio(tmp_path / "track.wav")
    res = StemAnalysisResult(
        breakdowns=[Breakdown(start_s=25.0, end_s=33.0, kind="silence", drum_rms_ratio=0.05)],
        drops=[Drop(start_s=33.0, energy_ratio=0.88, is_fake_drop_corrected=True, offset_s=2.0)],
        first_kick_s=0.5,
    )
    assert cues.write_stem_cues(p, res) is True

    assert cues.read_first_kick_tag(p) == pytest.approx(0.5, abs=0.001)
    assert cues.read_drop_tag(p) == pytest.approx(33.0, abs=0.001)
    assert cues.read_breakdown_tag(p) == pytest.approx(25.0, abs=0.001)
    assert cues.read_breakdown_kind_tag(p) == "silence"


def test_build_xml_schreibt_drop_und_breakdown_cues():
    track = rb.InboxTrack(
        path=Path("/test/inbox/150/5A/track.wav"),
        title="Test Track",
        artist="Artist",
        genre="Schranz",
        bpm=150.0,
        key="5A",
        comment="",
        duration_s=300.0,
        size=1000,
        added=datetime(2026, 10, 14, 12, 0),
        first_kick_s=0.5,
        drop_s=45.0,
        breakdown_s=30.0,
        breakdown_kind="groove",
    )

    xml = rb.build_xml([track], PATH_MAP, hot_cues=True, memory_cues=True)
    root = _parse(xml)
    marks = root.findall("COLLECTION/TRACK/POSITION_MARK")

    # 3 Punkte mit je Hot Cue + Memory Cue = 6 Marken
    assert len(marks) == 6

    by_num = {m.get("Num"): m.attrib for m in marks if m.get("Num") != "-1"}
    memories = [m.attrib for m in marks if m.get("Num") == "-1"]

    # Pad A (Num 0): First Kick, Grün
    assert by_num["0"]["Name"] == "First Kick"
    assert by_num["0"]["Start"] == "0.500"
    assert by_num["0"]["Red"] == "0" and by_num["0"]["Green"] == "255" and by_num["0"]["Blue"] == "0"

    # Pad B (Num 1): Drop, Orange
    assert by_num["1"]["Name"] == "Drop"
    assert by_num["1"]["Start"] == "45.000"
    assert by_num["1"]["Red"] == "255" and by_num["1"]["Green"] == "165" and by_num["1"]["Blue"] == "0"

    # Pad D (Num 3): Break (Groove), Gelb
    assert by_num["3"]["Name"] == "Break (Groove)"
    assert by_num["3"]["Start"] == "30.000"
    assert by_num["3"]["Red"] == "255" and by_num["3"]["Green"] == "255" and by_num["3"]["Blue"] == "0"

    # Memory Cues
    mem_names = {m["Name"]: m["Start"] for m in memories}
    assert mem_names["First Kick"] == "0.500"
    assert mem_names["Drop"] == "45.000"
    assert mem_names["Break (Groove)"] == "30.000"


def test_build_xml_breakdown_silence_name():
    track = rb.InboxTrack(
        path=Path("/test/inbox/150/5A/track.wav"),
        title="Silence Track",
        artist="Artist",
        genre="Schranz",
        bpm=150.0,
        key="5A",
        comment="",
        duration_s=300.0,
        size=1000,
        added=datetime(2026, 10, 14, 12, 0),
        breakdown_s=32.0,
        breakdown_kind="silence",
    )
    xml = rb.build_xml([track], PATH_MAP, hot_cues=True, memory_cues=False)
    root = _parse(xml)
    marks = root.findall("COLLECTION/TRACK/POSITION_MARK")
    assert len(marks) == 1
    assert marks[0].get("Name") == "Break (Silence)"
    assert marks[0].get("Num") == "3"
    assert marks[0].get("Start") == "32.000"


def test_build_xml_style_comments_mit_breakdown_kind():
    track = rb.InboxTrack(
        path=Path("/test/inbox/154/5A/track.wav"),
        title="Donk Anthem",
        artist="Artist",
        genre="bounce",
        bpm=154.0,
        key="5A",
        comment="Alt",
        duration_s=300.0,
        size=1000,
        added=datetime(2026, 10, 14, 12, 0),
        breakdown_kind="silence",
    )
    xml = rb.build_xml([track], PATH_MAP, style_comments=True)
    root = _parse(xml)
    t_node = root.find("COLLECTION/TRACK")
    assert "Break: Silence" in t_node.get("Comments")
