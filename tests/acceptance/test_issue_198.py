"""Akzeptanztests: bei Dubletten die getaggte Datei bevorzugen (Key oder BPM vorhanden)."""
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from sc_digger import rekordbox as rb
from sc_digger.rekordbox import InboxTrack, dedupe_by_soundcloud_id

PATH_MAP = {"/music/inbox": "Z:/Inbox"}


def mk(rel: str, key: str = "", bpm: float | None = None, title: str = "T") -> InboxTrack:
    return InboxTrack(path=Path("/music/inbox") / rel, title=title, artist="A", genre="", bpm=bpm,
                      key=key, comment="", duration_s=100.0, size=1, added=datetime(2026, 9, 29))


def names(tracks):
    return [t.path.name for t in tracks]


def test_getaggte_kopie_schlaegt_ungetaggtes_original():
    tracks = [mk("a/[1] A - X.wav"), mk("a/[1] A - X_1.wav", key="7B", bpm=152.0)]
    assert names(dedupe_by_soundcloud_id(tracks)) == ["[1] A - X_1.wav"]


def test_nur_bpm_oder_nur_key_zaehlt_als_getaggt():
    assert names(dedupe_by_soundcloud_id([mk("a/[1] A - X.wav"), mk("a/[1] A - X_1.wav", bpm=150.0)])) == ["[1] A - X_1.wav"]
    assert names(dedupe_by_soundcloud_id([mk("a/[1] A - X.wav"), mk("a/[1] A - X_1.wav", key="3A")])) == ["[1] A - X_1.wav"]


def test_key_nur_aus_leerzeichen_ist_ungetaggt():
    tracks = [mk("a/[1] A - X.wav"), mk("a/[1] A - X_1.wav", key="  ")]
    assert names(dedupe_by_soundcloud_id(tracks)) == ["[1] A - X.wav"]


def test_getaggt_im_root_schlaegt_ungetaggt_sortiert():
    tracks = [mk("150-155/6B/[2] D - S.wav"), mk("[2] D - S.wav", key="6B", bpm=152.0)]
    out = dedupe_by_soundcloud_id(tracks)
    assert [t.path.as_posix() for t in out] == ["/music/inbox/[2] D - S.wav"]


def test_beide_getaggt_original_gewinnt_wie_vorher():
    tracks = [mk("a/[3] A - Y_2.wav", key="1A", bpm=1.0), mk("a/[3] A - Y.wav", key="1A", bpm=1.0)]
    assert names(dedupe_by_soundcloud_id(tracks)) == ["[3] A - Y.wav"]


def test_beide_ungetaggt_original_gewinnt_wie_vorher():
    tracks = [mk("a/[4] A - Z_1.wav"), mk("a/[4] A - Z.wav")]
    assert names(dedupe_by_soundcloud_id(tracks)) == ["[4] A - Z.wav"]


def test_build_xml_nimmt_key_und_bpm_der_getaggten_datei():
    tracks = [mk("a/[5] A - W.wav", title="W"), mk("a/[5] A - W_1.wav", key="7B", bpm=152.0, title="W")]
    root = ET.fromstring(rb.build_xml(tracks, PATH_MAP))
    (t,) = root.find("COLLECTION").findall("TRACK")
    assert t.get("Tonality") == "7B"
    assert t.get("AverageBpm") == "152.00"
