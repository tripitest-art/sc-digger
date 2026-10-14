"""Eigene Tests für Rekordbox-XML-Export (sc_digger.rekordbox)."""
from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pytest

from sc_digger import rekordbox as rb
from sc_digger.models import Config
from sc_digger.organize import write_tags


def _create_audio(path: Path, **tags) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ["-b:a", "320k"] if path.suffix == ".mp3" else []
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=d=2:c=pink:r=44100:a=0.2",
            "-ac",
            "2",
            *args,
            str(path),
        ],
        check=True,
    )
    if tags:
        write_tags(path, **tags)
    return path


def test_m4a_reads_title_and_artist_and_tags(tmp_path):
    inbox = tmp_path / "inbox"
    p = inbox / "150-155" / "8A" / "Test - M4A Track.m4a"
    _create_audio(
        p,
        artist="Test Artist",
        title="M4A Title",
        bpm=154.0,
        genre="Hardtechno",
        comment="Test Comment",
    )

    tracks = rb.scan_inbox(inbox)
    assert len(tracks) == 1
    t = tracks[0]
    assert t.title == "M4A Title"
    assert t.artist == "Test Artist"
    assert t.genre == "Hardtechno"
    assert t.bpm == 154.0
    assert "Test Comment" in t.comment
    assert 1.5 < t.duration_s < 2.5
    assert t.size == p.stat().st_size


def test_empty_inbox_creates_valid_xml_with_zero_entries_and_empty_folder(tmp_path):
    inbox = tmp_path / "empty_inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    tracks = rb.scan_inbox(inbox)
    assert tracks == []

    mp = {str(inbox): "Z:/Highres/_sc-digger-inbox"}
    xml_str = rb.build_xml(tracks, mp)
    root = ET.fromstring(xml_str)

    assert root.tag == "DJ_PLAYLISTS" and root.get("Version") == "1.0.0"
    coll = root.find("COLLECTION")
    assert coll is not None
    assert coll.get("Entries") == "0"
    assert len(coll.findall("TRACK")) == 0

    top = root.find("PLAYLISTS/NODE")
    assert top is not None
    assert top.get("Name") == "ROOT" and top.get("Type") == "0" and top.get("Count") == "1"

    folder = top.find("NODE")
    assert folder is not None
    assert folder.get("Name") == "sc-digger" and folder.get("Type") == "0"
    assert folder.get("Count") == "0"
    assert len(folder.findall("NODE")) == 0


def test_unreadable_or_corrupt_file_logs_warning_and_uses_filename(tmp_path, caplog):
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    corrupt = inbox / "Corrupt - Audio.wav"
    corrupt.write_bytes(b"not a valid wav file header")

    tracks = rb.scan_inbox(inbox)
    assert len(tracks) == 1
    t = tracks[0]
    assert t.title == "Corrupt - Audio"
    assert t.artist == ""
    assert t.genre == ""
    assert t.bpm is None
    assert t.duration_s == 0.0
    assert t.size == len(b"not a valid wav file header")
    assert any("Nicht lesbare Datei" in r.message or "Konnte Datei" in r.message for r in caplog.records)


def test_laptop_location_root_mapping_and_trailing_slashes():
    mp = {"/": "Z:/All"}
    assert rb.laptop_location("/music/track.wav", mp) == "file://localhost/Z:/All/music/track.wav"

    mp_slashes = {"/music/inbox/": "Z:\\Highres\\_sc-digger-inbox\\"}
    assert (
        rb.laptop_location("/music/inbox/sub/track.mp3", mp_slashes)
        == "file://localhost/Z:/Highres/_sc-digger-inbox/sub/track.mp3"
    )


def test_aiff_reading_and_attributes(tmp_path):
    inbox = tmp_path / "inbox"
    aiff_path = inbox / "Artist - AIFF Track.aiff"
    _create_audio(aiff_path, artist="AIFF Artist", title="AIFF Title", bpm=158, key_name="4A", genre="Schranz")

    tracks = rb.scan_inbox(inbox)
    assert len(tracks) == 1
    t = tracks[0]
    assert t.title == "AIFF Title"
    assert t.artist == "AIFF Artist"
    assert t.bpm == 158.0
    assert t.key == "4A"
    assert t.genre == "Schranz"


ROOT = Path(__file__).resolve().parents[1]
PATH_MAP = {"/music/inbox": "Z:/Inbox"}


def _parse(xml_str: str) -> ET.Element:
    return ET.fromstring(xml_str.split("\n", 1)[1])


def _stem_track(
    name: str,
    *,
    first_kick_s: float | None = None,
    drop_s: float | None = None,
    breakdown_s: float | None = None,
    breakdown_kind: str | None = None,
) -> rb.InboxTrack:
    return rb.InboxTrack(
        path=Path("/music/inbox") / "x" / f"{name}.mp3",
        title=name,
        artist="A",
        genre="Schranz",
        bpm=152.0,
        key="5A",
        comment="",
        duration_s=300.0,
        size=1000,
        added=datetime(2026, 10, 14, 12, 0),
        first_kick_s=first_kick_s,
        drop_s=drop_s,
        breakdown_s=breakdown_s,
        breakdown_kind=breakdown_kind,
    )


def _marks(xml_str: str) -> list[ET.Element]:
    return _parse(xml_str).findall("COLLECTION/TRACK/POSITION_MARK")


def test_scan_inbox_liest_drop_und_breakdown_tags(tmp_path):
    from sc_digger import cues

    inbox = tmp_path / "inbox"
    mit = _create_audio(inbox / "a" / "mit.mp3")
    _create_audio(inbox / "a" / "ohne.mp3")
    assert cues.write_drop_tag(mit, 45.0) is True
    assert cues.write_breakdown_tag(mit, 30.0, "groove") is True

    by_name = {t.path.name: t for t in rb.scan_inbox(inbox)}
    assert by_name["mit.mp3"].drop_s == pytest.approx(45.0, abs=0.0005)
    assert by_name["mit.mp3"].breakdown_s == pytest.approx(30.0, abs=0.0005)
    assert by_name["mit.mp3"].breakdown_kind == "groove"
    assert by_name["ohne.mp3"].drop_s is None
    assert by_name["ohne.mp3"].breakdown_s is None
    assert by_name["ohne.mp3"].breakdown_kind is None


def test_inboxtrack_hat_drop_breakdown_standard_none():
    t = rb.InboxTrack(path=Path("/x.mp3"), title="t", artist="a", genre="g", bpm=None, key="",
                      comment="", duration_s=1.0, size=1, added=datetime(2026, 10, 14))
    assert t.drop_s is None
    assert t.breakdown_s is None
    assert t.breakdown_kind is None


def test_build_xml_schreibt_drop_und_breakdown_marks():
    marks = _marks(rb.build_xml([_stem_track("t", drop_s=45.0, breakdown_s=30.0)], PATH_MAP))
    hot = {m.get("Num"): m.attrib for m in marks if m.get("Num") != "-1"}
    assert hot["1"]["Name"] == "Drop"
    assert (hot["1"]["Red"], hot["1"]["Green"], hot["1"]["Blue"]) == ("255", "165", "0")
    assert hot["3"]["Name"] == "Break (Silence)"
    assert (hot["3"]["Red"], hot["3"]["Green"], hot["3"]["Blue"]) == ("255", "255", "0")
    # Memory Cues mit gleichem Namen und Start, aber ohne Farbe.
    mem = {m.get("Name"): m.attrib for m in marks if m.get("Num") == "-1"}
    assert mem["Drop"]["Start"] == "45.000" and "Red" not in mem["Drop"]
    assert mem["Break (Silence)"]["Start"] == "30.000"


def test_build_xml_breakdown_kind_groove_heisst_groove():
    marks = _marks(
        rb.build_xml(
            [_stem_track("t", breakdown_s=30.0, breakdown_kind="groove")],
            PATH_MAP,
            memory_cues=False,
        )
    )
    assert len(marks) == 1
    assert marks[0].get("Name") == "Break (Groove)"
    assert marks[0].get("Num") == "3"


def test_build_xml_marks_chronologisch_hot_vor_memory():
    marks = _marks(
        rb.build_xml(
            [_stem_track("t", first_kick_s=45.0, drop_s=10.0, breakdown_s=30.0)],
            PATH_MAP,
        )
    )
    assert [(m.get("Start"), m.get("Num")) for m in marks] == [
        ("10.000", "1"),
        ("10.000", "-1"),
        ("30.000", "3"),
        ("30.000", "-1"),
        ("45.000", "0"),
        ("45.000", "-1"),
    ]


def test_build_xml_nur_drop_als_hot_cue():
    marks = _marks(
        rb.build_xml([_stem_track("t", drop_s=12.0)], PATH_MAP, memory_cues=False)
    )
    assert [(m.get("Name"), m.get("Num"), m.get("Start")) for m in marks] == [
        ("Drop", "1", "12.000")
    ]


@pytest.mark.parametrize("hot,mem", [(False, False), (True, False), (False, True)])
def test_build_xml_cue_schalter_wirken_auf_alle_punkte(hot, mem):
    marks = _marks(
        rb.build_xml(
            [_stem_track("t", first_kick_s=6.4, drop_s=45.0, breakdown_s=30.0)],
            PATH_MAP,
            hot_cues=hot,
            memory_cues=mem,
        )
    )
    expected = (3 if hot else 0) + (3 if mem else 0)
    assert len(marks) == expected
    if hot:
        assert {m.get("Num") for m in marks if m.get("Num") != "-1"} == {"0", "1", "3"}
    if mem:
        assert len([m for m in marks if m.get("Num") == "-1"]) == 3


def test_build_xml_style_comments_haengt_break_kind_an():
    root = _parse(
        rb.build_xml([_stem_track("t", breakdown_kind="groove")], PATH_MAP, style_comments=True)
    )
    assert root.find("COLLECTION/TRACK").get("Comments").endswith("· Break: Groove")


def _cfg(tmp_path, **rekordbox_opts):
    from sc_digger import cues

    inbox = tmp_path / "inbox"
    coll = tmp_path / "coll"
    inbox.mkdir()
    coll.mkdir()
    p = _create_audio(inbox / "150-155" / "5A" / "a.mp3")
    assert cues.write_first_kick_tag(p, 0.5) is True
    assert cues.write_drop_tag(p, 45.0) is True
    assert cues.write_breakdown_tag(p, 30.0, "silence") is True
    cfg = Config(dict(Config.load(ROOT / "config.yaml").raw))
    cfg.raw["download"] = {**cfg["download"], "inbox_dir": str(inbox), "collection_dir": str(coll)}
    cfg.raw["rekordbox"] = {
        "xml_enabled": True,
        "xml_path": str(tmp_path / "sc-digger.xml"),
        "path_map": {str(inbox): "Z:/Inbox"},
        **rekordbox_opts,
    }
    return cfg


def test_write_rekordbox_xml_schreibt_stem_cues(tmp_path):
    root = ET.parse(rb.write_rekordbox_xml(_cfg(tmp_path, cues_enabled=True))).getroot()
    marks = root.findall("COLLECTION/TRACK/POSITION_MARK")
    assert len(marks) == 6
    assert {m.get("Num") for m in marks} == {"0", "1", "3", "-1"}


def test_write_rekordbox_xml_cues_disabled_verwirft_stem_cues(tmp_path):
    root = ET.parse(rb.write_rekordbox_xml(_cfg(tmp_path, cues_enabled=False))).getroot()
    assert root.find("COLLECTION/TRACK") is not None
    assert root.findall("COLLECTION/TRACK/POSITION_MARK") == []
