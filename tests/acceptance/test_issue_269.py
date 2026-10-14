from datetime import datetime
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import pytest
from sc_digger import cues, phrase
from sc_digger import rekordbox as rb

PATH_MAP = {"/test/inbox": "Z:/Inbox"}


def _parse_xml(xml_str: str) -> ET.Element:
    return ET.fromstring(xml_str)


def test_is_off_phrase_erkennt_ungerade_phrasen():
    bpm = 150.0  # beat = 0.4s
    first_kick = 0.0

    # 32 Beats (12.8s) -> On-Phrase (False)
    assert phrase.is_off_phrase(12.8, first_kick, bpm) is False
    # 16 Beats (6.4s) -> On-Phrase (False)
    assert phrase.is_off_phrase(6.4, first_kick, bpm) is False
    # 32.5 Beats (13.0s, Abweichung 0.5 Beats <= 1.75 Toleranz) -> On-Phrase (False)
    assert phrase.is_off_phrase(13.0, first_kick, bpm) is False
    # 56 Beats (14 Takte, 22.4s, Abweichung 8 Beats > 1.75) -> Off-Phrase (True)
    assert phrase.is_off_phrase(22.4, first_kick, bpm) is True

    # Kantenfälle: vor first_kick, bpm <= 0 -> immer False
    assert phrase.is_off_phrase(-1.0, first_kick, bpm) is False
    assert phrase.is_off_phrase(10.0, first_kick, 0.0) is False


def test_build_xml_phrase_warning_schreibt_warn_memory_cue():
    # 150 BPM, Kick bei 0.0s. Drop bei 22.4s (56 Beats = 14 Takte -> off-phrase)
    track = rb.InboxTrack(
        path=Path("/test/inbox/150/5A/off_track.wav"),
        title="Off-Phrase Track",
        artist="Artist",
        genre="Schranz",
        bpm=150.0,
        key="5A",
        comment="",
        duration_s=240.0,
        size=1000,
        added=datetime(2026, 10, 14, 12, 0),
        first_kick_s=0.0,
        drop_s=22.4,
    )

    # Mit phrase_warning=True
    xml_warn = rb.build_xml([track], PATH_MAP, hot_cues=True, memory_cues=True, phrase_warning=True)
    root_warn = _parse_xml(xml_warn)
    marks_warn = root_warn.findall("COLLECTION/TRACK/POSITION_MARK")

    # Erwartet: Hot Cue Drop, Memory Cue Drop, sowie Warn-Cue "⚠️ Off-Phrase"
    warn_cues = [m for m in marks_warn if m.get("Name") == "⚠️ Off-Phrase"]
    assert len(warn_cues) == 1
    w = warn_cues[0]
    assert w.get("Num") == "-1"
    assert w.get("Type") == "0"
    assert w.get("Start") == "22.400"
    assert (w.get("Red"), w.get("Green"), w.get("Blue")) == ("255", "0", "0")

    # Ohne phrase_warning=False kein Warn-Cue
    xml_clean = rb.build_xml([track], PATH_MAP, hot_cues=True, memory_cues=True, phrase_warning=False)
    root_clean = _parse_xml(xml_clean)
    marks_clean = root_clean.findall("COLLECTION/TRACK/POSITION_MARK")
    assert not any(m.get("Name") == "⚠️ Off-Phrase" for m in marks_clean)


def test_build_xml_active_loop_notbremse():
    bpm = 150.0  # beat = 0.4s
    duration = 240.0
    first_kick = 0.0
    # 16 Beats = 6.4s Loop
    track = rb.InboxTrack(
        path=Path("/test/inbox/150/5A/loop_track.wav"),
        title="Loop Track",
        artist="Artist",
        genre="Schranz",
        bpm=bpm,
        key="5A",
        comment="",
        duration_s=duration,
        size=1000,
        added=datetime(2026, 10, 14, 12, 0),
        first_kick_s=first_kick,
        drop_s=32.0,
    )

    xml = rb.build_xml([track], PATH_MAP, active_loop=True, active_loop_beats=16)
    root = _parse_xml(xml)
    marks = root.findall("COLLECTION/TRACK/POSITION_MARK")

    loop_marks = [m for m in marks if m.get("Name") == "Active Loop"]
    assert len(loop_marks) == 1
    lm = loop_marks[0]
    assert lm.get("Num") == "-1"
    assert lm.get("Type") in ("1", "4")
    assert lm.get("Active") == "1"
    start_s = float(lm.get("Start"))
    end_s = float(lm.get("End"))
    assert end_s <= duration
    assert round(end_s - start_s, 3) == 6.400


def test_build_xml_countdown_meilenstein_drop2():
    track = rb.InboxTrack(
        path=Path("/test/inbox/150/5A/track_drop2.wav"),
        title="Drop2 Track",
        artist="Artist",
        genre="Schranz",
        bpm=150.0,
        key="5A",
        comment="",
        duration_s=300.0,
        size=1000,
        added=datetime(2026, 10, 14, 12, 0),
        first_kick_s=0.0,
        drop_s=64.0,
        drop2_s=128.0,
    )

    xml = rb.build_xml([track], PATH_MAP, hot_cues=True, memory_cues=True)
    root = _parse_xml(xml)
    marks = root.findall("COLLECTION/TRACK/POSITION_MARK")

    drop2_hot = [m for m in marks if m.get("Name") == "Drop 2" and m.get("Num") != "-1"]
    drop2_mem = [m for m in marks if m.get("Name") == "Drop 2" and m.get("Num") == "-1"]
    assert len(drop2_hot) == 1
    assert len(drop2_mem) == 1
    assert drop2_mem[0].get("Start") == "128.000"


def _create_audio(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "anoisesrc=d=1:c=pink:r=44100:a=0.2", "-ac", "2", "-b:a", "320k", str(path)],
        check=True,
    )
    return path


def test_tag_konstanten():
    assert cues.DROP2_TAG == "SCDIGGER_DROP2"


def test_cues_drop2_tag_roundtrip(tmp_path):
    p = _create_audio(tmp_path / "test.mp3")
    assert cues.read_drop2_tag(p) is None
    assert cues.write_drop2_tag(p, 128.5) is True
    assert cues.read_drop2_tag(p) == pytest.approx(128.5, abs=0.001)
