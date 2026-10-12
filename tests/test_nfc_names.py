"""Eigene Tests zu #199: NFC-Namen in Inbox und Rekordbox-Location.

Ergänzen die Akzeptanztests (tests/acceptance/test_issue_199.py) um Randfälle,
die im Issue beschrieben, aber dort nicht abgedeckt sind.
"""
import unicodedata
from pathlib import Path

from sc_digger.organize import organize
from sc_digger.rekordbox import laptop_location

NFD = unicodedata.normalize("NFD", "[7] 42Q - Für immer.wav")
NFC = unicodedata.normalize("NFC", NFD)


def test_laptop_location_ohne_treffer_none():
    assert laptop_location(Path("/music/inbox/a.wav"), {"/music/other": "C:/X"}) is None


def test_laptop_location_ascii_unveraendert():
    loc = laptop_location("/music/inbox/a.wav", {"/music/inbox": "C:/Inbox"})
    assert loc == "file://localhost/C:/Inbox/a.wav"


def test_organize_unknown_key_pfad_in_nfc(tmp_path):
    src = tmp_path / NFD
    src.write_bytes(b"x")
    out = organize(src, tmp_path, bpm=None, key_camelot=None)
    assert out.name == NFC
    assert out.parent == tmp_path / "unknown-bpm" / "unknown-key"
    assert out.exists()


def test_organize_ascii_name_unveraendert(tmp_path):
    src = tmp_path / "track.wav"
    src.write_bytes(b"x")
    out = organize(src, tmp_path, bpm=152.0, key_camelot="8A")
    assert out.name == "track.wav"
    assert out == tmp_path / "150-155" / "8A" / "track.wav"
