"""Akzeptanztests: Dateinamen in der Inbox und Locations in der XML in NFC (zusammengesetzte Umlaute)."""
import unicodedata
from pathlib import Path

from sc_digger.organize import organize
from sc_digger.rekordbox import laptop_location

NFD = unicodedata.normalize("NFD", "[7] 42Q - Für immer.wav")
NFC = unicodedata.normalize("NFC", NFD)


def test_testdaten_sind_wirklich_verschieden():
    assert NFD != NFC and len(NFD) == len(NFC) + 1


def test_location_in_nfc():
    loc = laptop_location(Path("/music/inbox/150-155/8A") / NFD, {"/music/inbox": "C:/Inbox"})
    assert loc == "file://localhost/C:/Inbox/150-155/8A/%5B7%5D%2042Q%20-%20F%C3%BCr%20immer.wav"


def test_location_bleibt_bei_nfc_namen_gleich():
    loc = laptop_location(Path("/music/inbox") / NFC, {"/music/inbox": "C:/Inbox"})
    assert loc == "file://localhost/C:/Inbox/%5B7%5D%2042Q%20-%20F%C3%BCr%20immer.wav"


def test_pfad_praefix_wird_ebenfalls_normalisiert():
    mp = {"/music/inbox": unicodedata.normalize("NFD", "C:/Müsik")}
    loc = laptop_location("/music/inbox/a.wav", mp)
    assert loc == "file://localhost/C:/M%C3%BCsik/a.wav"


def test_organize_benennt_beim_verschieben_in_nfc_um(tmp_path):
    src = tmp_path / NFD
    src.write_bytes(b"x")
    out = organize(src, tmp_path, bpm=152.0, key_camelot="8A")
    assert out.name == NFC
    assert out.parent == tmp_path / "150-155" / "8A"
    assert out.exists()
    assert not src.exists()
    assert [p.name for p in out.parent.iterdir()] == [NFC]


def test_organize_benennt_auch_in_zielordner_um(tmp_path):
    d = tmp_path / "150-155" / "8A"
    d.mkdir(parents=True)
    src = d / NFD
    src.write_bytes(b"x")
    out = organize(src, tmp_path, bpm=152.0, key_camelot="8A")
    assert out == d / NFC
    assert [p.name for p in d.iterdir()] == [NFC]


def test_organize_nfc_name_unveraendert(tmp_path):
    src = tmp_path / NFC
    src.write_bytes(b"x")
    out = organize(src, tmp_path, bpm=152.0, key_camelot="8A")
    assert out.name == NFC


def test_organize_kollision_nach_normalisierung_gibt_endung(tmp_path):
    d = tmp_path / "150-155" / "8A"
    d.mkdir(parents=True)
    (d / NFC).write_bytes(b"alt")
    src = tmp_path / NFD
    src.write_bytes(b"neu")
    out = organize(src, tmp_path, bpm=152.0, key_camelot="8A")
    assert out == d / unicodedata.normalize("NFC", "[7] 42Q - Für immer_1.wav")
    assert (d / NFC).read_bytes() == b"alt"
    assert out.read_bytes() == b"neu"
