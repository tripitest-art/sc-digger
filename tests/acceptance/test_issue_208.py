"""Akzeptanztests: Rekordbox-XML Hot Cues und Memory Cues konfigurierbar."""
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
import pytest

from sc_digger import rekordbox as rb
from sc_digger.models import Config

PATH_MAP = {"/music/inbox": "Z:/Inbox"}


def _t(name: str, first_kick_s: float | None = 6.4) -> rb.InboxTrack:
    return rb.InboxTrack(
        path=Path("/music/inbox") / "x" / f"{name}.mp3",
        title=name, artist="Artist", genre="Schranz",
        bpm=152.0, key="5A", comment="", duration_s=300.0, size=1000,
        added=datetime(2026, 10, 12, 12, 0), first_kick_s=first_kick_s,
    )


def _parse(xml_str: str) -> ET.Element:
    return ET.fromstring(xml_str.split("\n", 1)[1])


def test_standard_schreibt_hotcue_und_memorycue():
    xml = rb.build_xml([_t("t1", 6.4)], PATH_MAP, hot_cues=True, memory_cues=True)
    root = _parse(xml)
    marks = root.findall("COLLECTION/TRACK/POSITION_MARK")
    assert len(marks) == 2
    # Hot Cue A (Num="0") mit Grün
    hc = marks[0].attrib
    assert hc["Name"] == "First Kick"
    assert hc["Type"] == "0"
    assert hc["Start"] == "6.400"
    assert hc["Num"] == "0"
    assert hc["Red"] == "0"
    assert hc["Green"] == "255"
    assert hc["Blue"] == "0"
    # Memory Cue (Num="-1") ohne Farbe
    mc = marks[1].attrib
    assert mc["Name"] == "First Kick"
    assert mc["Type"] == "0"
    assert mc["Start"] == "6.400"
    assert mc["Num"] == "-1"
    assert "Red" not in mc


def test_nur_hot_cue():
    xml = rb.build_xml([_t("t1", 6.4)], PATH_MAP, hot_cues=True, memory_cues=False)
    root = _parse(xml)
    marks = root.findall("COLLECTION/TRACK/POSITION_MARK")
    assert len(marks) == 1
    assert marks[0].attrib["Num"] == "0"
    assert marks[0].attrib["Red"] == "0"


def test_nur_memory_cue():
    xml = rb.build_xml([_t("t1", 6.4)], PATH_MAP, hot_cues=False, memory_cues=True)
    root = _parse(xml)
    marks = root.findall("COLLECTION/TRACK/POSITION_MARK")
    assert len(marks) == 1
    assert marks[0].attrib["Num"] == "-1"
    assert "Red" not in marks[0].attrib


def test_weder_hot_noch_memory_schreibt_keine_marks():
    xml = rb.build_xml([_t("t1", 6.4)], PATH_MAP, hot_cues=False, memory_cues=False)
    root = _parse(xml)
    assert root.findall("COLLECTION/TRACK/POSITION_MARK") == []


def test_track_ohne_first_kick_hat_keine_marks():
    xml = rb.build_xml([_t("t1", None)], PATH_MAP, hot_cues=True, memory_cues=True)
    root = _parse(xml)
    assert root.findall("COLLECTION/TRACK/POSITION_MARK") == []


def test_config_schema_kennt_neue_optionen():
    from sc_digger.schema import FIELDS
    paths = {f.path for f in FIELDS}
    assert "rekordbox.cues_as_hotcues" in paths
    assert "rekordbox.cues_as_memory" in paths
