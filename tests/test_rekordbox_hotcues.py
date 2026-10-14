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


def _full_track(path: Path) -> rb.InboxTrack:
    """Track mit allen Sektionen für das 8-Pad-Layout (150 BPM -> Beat 0.4 s)."""
    return rb.InboxTrack(
        path=path,
        title="T", artist="A", genre="Schranz", bpm=150.0, key="5A",
        comment="", duration_s=300.0, size=1000,
        added=datetime(2026, 10, 14, 12, 0), first_kick_s=0.0,
        drop_s=60.0, breakdown_s=30.0, breakdown_kind="groove", drop2_s=90.0,
        outro_s=250.0, percussion_loop_s=40.0, snare_fill_s=59.6,
    )


def _run(tmp_path, monkeypatch, track=None, **rb_opts):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    mp3 = inbox / "[1] t.mp3"
    mp3.write_bytes(b"\x00")
    monkeypatch.setattr(rb, "scan_inbox", lambda _inbox: [track(mp3) if track else _track(mp3)])

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


def _by_num(marks):
    # Num -> Attribute; vergleichbar ohne Annahme über die Reihenfolge im XML.
    return {m.attrib["Num"]: m.attrib for m in marks}


def test_config_hot_cue_layout_standard_8pad_belegt_alle_pads(tmp_path, monkeypatch):
    marks = _run(tmp_path, monkeypatch, track=_full_track, hot_cue_layout="standard_8pad",
                 cues_as_hotcues=True, cues_as_memory=False)
    hot = _by_num(marks)
    assert sorted(hot, key=int) == ["0", "1", "2", "3", "4", "5", "6", "7"]
    assert (hot["0"]["Name"], hot["0"]["Start"]) == ("Main Entry", "8.800")
    assert (hot["1"]["Name"], hot["1"]["Start"]) == ("Late Entry", "34.400")
    assert (hot["2"]["Name"], hot["2"]["Start"]) == ("Drop 1", "60.000")
    assert (hot["3"]["Name"], hot["3"]["Start"]) == ("Percussion Loop", "40.000")
    assert (hot["4"]["Name"], hot["4"]["Start"]) == ("Break (Groove)", "30.000")
    assert (hot["5"]["Name"], hot["5"]["Start"]) == ("Drop 2", "90.000")
    assert (hot["6"]["Name"], hot["6"]["Start"]) == ("Snare Fill", "59.600")
    assert (hot["7"]["Name"], hot["7"]["Start"]) == ("Outro", "250.000")
    for num in hot:
        assert hot[num]["Type"] == "0"
        assert "Red" in hot[num] and "Green" in hot[num] and "Blue" in hot[num]
    assert (hot["0"]["Red"], hot["0"]["Green"], hot["0"]["Blue"]) == ("0", "255", "0")
    assert (hot["3"]["Red"], hot["3"]["Green"], hot["3"]["Blue"]) == ("0", "255", "255")
    assert (hot["6"]["Red"], hot["6"]["Green"], hot["6"]["Blue"]) == ("255", "0", "255")


def test_config_hot_cue_layout_standard_8pad_als_memory_cues(tmp_path, monkeypatch):
    marks = _run(tmp_path, monkeypatch, track=_full_track, hot_cue_layout="standard_8pad",
                 cues_as_hotcues=False, cues_as_memory=True)
    mem = [m.attrib for m in marks]
    assert len(mem) == 8
    assert {m["Num"] for m in mem} == {"-1"}
    assert all("Red" not in m for m in mem)


def test_default_layout_bleibt_legacy(tmp_path, monkeypatch):
    marks = _run(tmp_path, monkeypatch, track=_full_track,
                 cues_as_hotcues=True, cues_as_memory=False)
    hot = _by_num(marks)
    assert sorted(hot, key=int) == ["0", "1", "2", "3"]
    assert hot["0"]["Name"] == "First Kick"
    assert hot["1"]["Name"] == "Drop"
    assert hot["2"]["Name"] == "Drop 2"
    assert hot["3"]["Name"] == "Break (Groove)"


def test_unbekanntes_layout_faellt_auf_legacy_zurueck(tmp_path, monkeypatch, caplog):
    marks = _run(tmp_path, monkeypatch, track=_full_track, hot_cue_layout="quatsch",
                 cues_as_hotcues=True, cues_as_memory=False)
    hot = _by_num(marks)
    assert sorted(hot, key=int) == ["0", "1", "2", "3"]
    assert hot["0"]["Name"] == "First Kick"
    assert any("hot_cue_layout" in r.message for r in caplog.records)
