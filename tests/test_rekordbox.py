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
