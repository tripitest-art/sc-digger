"""Tests: write_rekordbox_xml steuert Hot Cues und Memory Cues über die Config."""
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from sc_digger import rekordbox as rb


def _track(path: Path) -> rb.InboxTrack:
    return rb.InboxTrack(
        path=path,
        title="T", artist="A", genre="Schranz", bpm=150.0, key="5A",
        comment="", duration_s=300.0, size=1000,
        added=datetime(2026, 10, 12, 12, 0), first_kick_s=6.4,
    )


def _run(tmp_path, monkeypatch, **rb_opts):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    mp3 = inbox / "[1] t.mp3"
    mp3.write_bytes(b"\x00")
    monkeypatch.setattr(rb, "scan_inbox", lambda _inbox: [_track(mp3)])

    xml_path = tmp_path / "out.xml"
    cfg = {
        "rekordbox": {
            "xml_enabled": True,
            "xml_path": str(xml_path),
            "path_map": {str(inbox): "Z:/Inbox"},
            "cues_enabled": True,
            **rb_opts,
        },
        "download": {"inbox_dir": str(inbox)},
    }
    out = rb.write_rekordbox_xml(cfg)
    assert out == xml_path
    root = ET.fromstring(xml_path.read_text(encoding="utf-8").split("\n", 1)[1])
    return root.findall("COLLECTION/TRACK/POSITION_MARK")


def test_defaults_schreiben_hot_und_memory(tmp_path, monkeypatch):
    marks = _run(tmp_path, monkeypatch)
    assert [m.attrib["Num"] for m in marks] == ["0", "-1"]
    assert marks[0].attrib["Green"] == "255"


def test_config_nur_hotcues(tmp_path, monkeypatch):
    marks = _run(tmp_path, monkeypatch, cues_as_hotcues=True, cues_as_memory=False)
    assert [m.attrib["Num"] for m in marks] == ["0"]


def test_config_nur_memory(tmp_path, monkeypatch):
    marks = _run(tmp_path, monkeypatch, cues_as_hotcues=False, cues_as_memory=True)
    assert [m.attrib["Num"] for m in marks] == ["-1"]


def test_config_beide_aus(tmp_path, monkeypatch):
    marks = _run(tmp_path, monkeypatch, cues_as_hotcues=False, cues_as_memory=False)
    assert marks == []


def test_cues_enabled_false_unterdrueckt_alles(tmp_path, monkeypatch):
    marks = _run(tmp_path, monkeypatch, cues_enabled=False,
                 cues_as_hotcues=True, cues_as_memory=True)
    assert marks == []
