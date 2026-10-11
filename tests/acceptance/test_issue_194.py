"""Akzeptanztests: Dubletten (gleiche SoundCloud-ID im Dateinamen) in der Rekordbox-XML."""
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from sc_digger import rekordbox as rb
from sc_digger.rekordbox import InboxTrack, dedupe_by_soundcloud_id, soundcloud_id_from_name

PATH_MAP = {"/music/inbox": "Z:/Inbox"}


def mk(rel: str, title: str = "T") -> InboxTrack:
    return InboxTrack(path=Path("/music/inbox") / rel, title=title, artist="A", genre="", bpm=162.0,
                      key="6A", comment="", duration_s=100.0, size=1, added=datetime(2026, 9, 29))


def test_id_aus_dateinamen():
    assert soundcloud_id_from_name("[2273961158] Gaiveu - Mix.wav") == "2273961158"
    assert soundcloud_id_from_name("[2273961158] Gaiveu - Mix_1.wav") == "2273961158"
    assert soundcloud_id_from_name("Ohne Id.wav") is None
    assert soundcloud_id_from_name("x [123] y.wav") is None


def test_original_gewinnt_vor_endung_n():
    tracks = [
        mk("160-165/11B/[1] A - X_2.wav"),
        mk("160-165/11B/[1] A - X.wav"),
        mk("160-165/11B/[1] A - X_1.wav"),
    ]
    out = dedupe_by_soundcloud_id(tracks)
    assert [t.path.name for t in out] == ["[1] A - X.wav"]


def test_ohne_original_gewinnt_kleinster_relativer_pfad():
    tracks = [mk("b/[7] A - Y_1.wav"), mk("a/[7] A - Y_2.wav")]
    out = dedupe_by_soundcloud_id(tracks)
    assert [t.path.as_posix() for t in out] == ["/music/inbox/a/[7] A - Y_2.wav"]


def test_reihenfolge_und_dateien_ohne_id_bleiben():
    tracks = [mk("x/Ohne.wav"), mk("x/[2] A - B.wav"), mk("x/Ohne2.wav"), mk("y/[2] A - B_1.wav"), mk("x/[3] C.wav")]
    out = dedupe_by_soundcloud_id(tracks)
    assert [t.path.name for t in out] == ["Ohne.wav", "[2] A - B.wav", "Ohne2.wav", "[3] C.wav"]


def test_sortierte_datei_gewinnt_vor_lose_im_root():
    tracks = [mk("[5] D - S.wav"), mk("150-155/6B/[5] D - S.wav")]
    out = dedupe_by_soundcloud_id(tracks)
    assert [t.path.as_posix() for t in out] == ["/music/inbox/150-155/6B/[5] D - S.wav"]


def test_leer():
    assert dedupe_by_soundcloud_id([]) == []


def test_build_xml_enthaelt_dublette_nur_einmal():
    tracks = [mk("a/[9] A - Z.wav", "Z"), mk("a/[9] A - Z_1.wav", "Z"), mk("a/[10] B.wav", "B")]
    root = ET.fromstring(rb.build_xml(tracks, PATH_MAP))
    coll = root.find("COLLECTION")
    assert coll.get("Entries") == "2"
    assert [t.get("Name") for t in coll.findall("TRACK")] == ["Z", "B"]
    week = root.find(".//NODE[@Type='1']")
    assert week.get("Entries") == "2"
    assert [t.get("Key") for t in week.findall("TRACK")] == ["1", "2"]
