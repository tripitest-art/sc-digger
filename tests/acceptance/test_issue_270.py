from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET
import pytest

from sc_digger import rekordbox as rb
from sc_digger.models import Config
from sc_digger.schema import get_field

PATH_MAP = {"/test/inbox": "Z:/Inbox"}


def _parse(xml_str: str) -> ET.Element:
    return ET.fromstring(xml_str.split("\n", 1)[1])


def test_standard_8pad_vollstaendiges_setup():
    # 150 BPM -> beat = 0.4s. 128 Beats = 51.2s, 64 Beats = 25.6s, 1 Beat = 0.4s.
    track = rb.InboxTrack(
        path=Path("/test/inbox/150/5A/track.wav"),
        title="Track Full",
        artist="Artist",
        genre="Schranz",
        bpm=150.0,
        key="5A",
        comment="",
        duration_s=300.0,
        size=1000,
        added=datetime(2026, 10, 14, 12, 0),
        first_kick_s=0.0,
        drop_s=60.0,
        breakdown_s=30.0,
        breakdown_kind="groove",
        drop2_s=90.0,
        outro_s=250.0,
        percussion_loop_s=40.0,
        snare_fill_s=59.6,
    )

    cues = rb.generate_hot_cues(track, layout="standard_8pad")
    # Mapping von Num -> (start_s, name, color)
    by_num = {num: (start_s, name, color) for start_s, num, name, color in cues}

    # Pad A: 128 Beats vor Drop 1 = 60.0 - 51.2 = 8.8s
    assert by_num["0"][0] == pytest.approx(8.8, abs=0.001)
    assert by_num["0"][1] == "Main Entry"
    assert by_num["0"][2] == ("0", "255", "0")

    # Pad B: 64 Beats vor Drop 1 = 60.0 - 25.6 = 34.4s
    assert by_num["1"][0] == pytest.approx(34.4, abs=0.001)
    assert by_num["1"][1] == "Late Entry"
    assert by_num["1"][2] == ("255", "255", "0")

    # Pad C: Drop 1
    assert by_num["2"][0] == pytest.approx(60.0, abs=0.001)
    assert by_num["2"][1] == "Drop 1"
    assert by_num["2"][2] == ("255", "0", "0")

    # Pad D: Percussion Loop
    assert by_num["3"][0] == pytest.approx(40.0, abs=0.001)
    assert by_num["3"][1] == "Percussion Loop"
    assert by_num["3"][2] == ("0", "255", "255")

    # Pad E: Breakdown (da Loop vorhanden ist)
    assert by_num["4"][0] == pytest.approx(30.0, abs=0.001)
    assert by_num["4"][1] == "Break (Groove)"
    assert by_num["4"][2] == ("255", "165", "0")

    # Pad F: Drop 2
    assert by_num["5"][0] == pytest.approx(90.0, abs=0.001)
    assert by_num["5"][1] == "Drop 2"
    assert by_num["5"][2] == ("255", "0", "0")

    # Pad G: Snare Fill
    assert by_num["6"][0] == pytest.approx(59.6, abs=0.001)
    assert by_num["6"][1] == "Snare Fill"
    assert by_num["6"][2] == ("255", "0", "255")

    # Pad H: Outro
    assert by_num["7"][0] == pytest.approx(250.0, abs=0.001)
    assert by_num["7"][1] == "Outro"
    assert by_num["7"][2] == ("255", "255", "255")


def test_standard_8pad_fallbacks_fuer_fehlende_sektionen():
    # 150 BPM. first_kick=6.4s, drop=25.6s (64 Beats ab 0.0), breakdown=51.2s (128 Beats)
    track = rb.InboxTrack(
        path=Path("/test/inbox/150/5A/track.wav"),
        title="Track Fallbacks",
        artist="Artist",
        genre="Schranz",
        bpm=150.0,
        key="5A",
        comment="",
        duration_s=180.0,
        size=1000,
        added=datetime(2026, 10, 14, 12, 0),
        first_kick_s=6.4,
        drop_s=25.6,
        breakdown_s=51.2,
        breakdown_kind="silence",
        drop2_s=None,
        outro_s=None,
        percussion_loop_s=None,
        snare_fill_s=None,
    )

    cues = rb.generate_hot_cues(track, layout="standard_8pad")
    by_num = {num: (start_s, name, color) for start_s, num, name, color in cues}

    # Pad A: 25.6 - 51.2 = -25.6 < first_kick_s (6.4) -> Fallback auf first_kick_s
    assert by_num["0"][0] == pytest.approx(6.4, abs=0.001)
    assert by_num["0"][1] == "Main Entry"
    assert by_num["0"][2] == ("0", "255", "0")

    # Pad B: 25.6 - 25.6 = 0.0 < first_kick_s (6.4) -> entfällt
    assert "1" not in by_num

    # Pad C: Drop 1
    assert by_num["2"][0] == pytest.approx(25.6, abs=0.001)
    assert by_num["2"][1] == "Drop 1"
    assert by_num["2"][2] == ("255", "0", "0")

    # Pad D: Loop fehlt -> Fallback auf Breakdown
    assert by_num["3"][0] == pytest.approx(51.2, abs=0.001)
    assert by_num["3"][1] == "Break (Silence)"
    assert by_num["3"][2] == ("0", "255", "255")

    # Pad E: entfällt, da Breakdown bereits auf Pad D liegt
    assert "4" not in by_num

    # Pad F: Drop 2 fehlt -> entfällt
    assert "5" not in by_num

    # Pad G: Snare Fill Fallback: 1 Beat vor Drop = 25.6 - 0.4 = 25.2s (>= first_kick 6.4)
    assert by_num["6"][0] == pytest.approx(25.2, abs=0.001)
    assert by_num["6"][1] == "Snare Fill"
    assert by_num["6"][2] == ("255", "0", "255")

    # Pad H: Outro Fallback: duration_s (180.0) - 64 Beats (25.6s) = 154.4s (> breakdown 51.2)
    assert by_num["7"][0] == pytest.approx(154.4, abs=0.001)
    assert by_num["7"][1] == "Outro"
    assert by_num["7"][2] == ("255", "255", "255")


def test_build_xml_standard_8pad_hot_und_memory_cues():
    track = rb.InboxTrack(
        path=Path("/test/inbox/150/5A/track.wav"),
        title="Track XML",
        artist="Artist",
        genre="Schranz",
        bpm=150.0,
        key="5A",
        comment="",
        duration_s=200.0,
        size=1000,
        added=datetime(2026, 10, 14, 12, 0),
        first_kick_s=0.0,
        drop_s=60.0,
        breakdown_s=30.0,
        breakdown_kind="groove",
        drop2_s=None,
        outro_s=None,
        percussion_loop_s=None,
        snare_fill_s=None,
    )

    xml_str = rb.build_xml([track], PATH_MAP, hot_cue_layout="standard_8pad", hot_cues=True, memory_cues=True)
    root = _parse(xml_str)
    marks = root.findall("COLLECTION/TRACK/POSITION_MARK")

    # Es müssen Hot Cues (Num 0..7) und Memory Cues (Num -1) vorhanden sein
    hot_marks = [m for m in marks if m.attrib["Num"] != "-1"]
    mem_marks = [m for m in marks if m.attrib["Num"] == "-1"]
    assert len(hot_marks) > 0
    assert len(hot_marks) == len(mem_marks)

    # Alle Hot Cues müssen Type="0", gültige Startzeit und RGB-Farben haben
    for m in hot_marks:
        assert m.attrib["Type"] == "0"
        assert float(m.attrib["Start"]) >= 0.0
        assert "Red" in m.attrib and "Green" in m.attrib and "Blue" in m.attrib

    # Memory Cues haben Type="0", Num="-1" und keine RGB-Farben
    for m in mem_marks:
        assert m.attrib["Type"] == "0"
        assert m.attrib["Num"] == "-1"
        assert "Red" not in m.attrib


def test_legacy_layout_bleibt_rueckwaertskompatibel():
    track = rb.InboxTrack(
        path=Path("/test/inbox/150/5A/track.wav"),
        title="Track Legacy",
        artist="Artist",
        genre="Schranz",
        bpm=150.0,
        key="5A",
        comment="",
        duration_s=200.0,
        size=1000,
        added=datetime(2026, 10, 14, 12, 0),
        first_kick_s=0.0,
        drop_s=60.0,
        breakdown_s=30.0,
        breakdown_kind="groove",
    )

    # Explizit legacy
    cues_legacy = rb.generate_hot_cues(track, layout="legacy")
    nums_legacy = [num for _, num, _, _ in cues_legacy]
    assert nums_legacy == ["0", "1", "3"]

    # build_xml ohne layout-Parameter nutzt legacy
    xml_default = rb.build_xml([track], PATH_MAP, hot_cues=True, memory_cues=False)
    root = _parse(xml_default)
    nums_default = [m.attrib["Num"] for m in root.findall("COLLECTION/TRACK/POSITION_MARK")]
    assert nums_default == ["0", "1", "3"]


def test_schema_und_config_hot_cue_layout():
    field = get_field("rekordbox.hot_cue_layout")
    assert field.kind == "str"
    assert len(field.label) <= 40
    assert field.help.strip() != ""

    cfg = Config.load(Path(__file__).resolve().parents[2] / "config.yaml")
    assert cfg.raw["rekordbox"]["hot_cue_layout"] in ("legacy", "standard_8pad")


def test_write_rekordbox_xml_mit_hot_cue_layout(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    track_file = inbox / "[1] track.mp3"
    track_file.write_bytes(b"\x00")

    track = rb.InboxTrack(
        path=track_file,
        title="Track",
        artist="Artist",
        genre="Schranz",
        bpm=150.0,
        key="5A",
        comment="",
        duration_s=200.0,
        size=1000,
        added=datetime(2026, 10, 14, 12, 0),
        first_kick_s=0.0,
        drop_s=60.0,
        breakdown_s=30.0,
        breakdown_kind="groove",
    )
    monkeypatch.setattr(rb, "scan_inbox", lambda _inbox: [track])

    xml_path = tmp_path / "out.xml"
    cfg = Config({
        "rekordbox": {
            "xml_enabled": True,
            "xml_path": str(xml_path),
            "path_map": {str(inbox): "Z:/Inbox"},
            "cues_enabled": True,
            "cues_as_hotcues": True,
            "cues_as_memory": False,
            "hot_cue_layout": "standard_8pad",
        },
        "download": {"inbox_dir": str(inbox)},
    })

    out = rb.write_rekordbox_xml(cfg)
    assert out == xml_path
    root = _parse(xml_path.read_text(encoding="utf-8"))
    nums = [m.attrib["Num"] for m in root.findall("COLLECTION/TRACK/POSITION_MARK")]
    # standard_8pad enthält bei vorhandenem drop_s und bpm u. a. Pad A (0), Pad B (1), Pad C (2)
    assert "2" in nums
