"""Akzeptanztests: Rekordbox-XML aus der Inbox (Pfade aus Sicht des DJ-Laptops)."""
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pytest

from sc_digger import main as m
from sc_digger import rekordbox as rb
from sc_digger.models import Config
from sc_digger.organize import write_tags

MAP = {"/music/inbox": "Z:\\Highres\\_sc-digger-inbox"}
NOW = datetime(2026, 9, 28, 22, 0)


def _audio(path: Path, **tags) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ["-b:a", "320k"] if path.suffix == ".mp3" else []
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=3:c=pink:r=44100:a=0.2",
                    "-ac", "2", *args, str(path)], check=True)
    if tags:
        write_tags(path, **tags)
    return path


def _added(path: Path, when: datetime) -> None:
    ts = time.mktime(when.timetuple())
    os.utime(path, (ts, ts))


# ---------------- Pfade für den Laptop ----------------
@pytest.mark.parametrize("server, expected", [
    ("/music/inbox/150-155/8A/Artist - Track.wav",
     "file://localhost/Z:/Highres/_sc-digger-inbox/150-155/8A/Artist%20-%20Track.wav"),
    ("/music/inbox/NØSS – Join me (Edit) & more.flac",
     "file://localhost/Z:/Highres/_sc-digger-inbox/N%C3%98SS%20%E2%80%93%20Join%20me%20%28Edit%29%20%26%20more.flac"),
    ("/music/inbox2/x.wav", None),          # nur an Pfadgrenzen
    ("/music/Schranz/x.wav", None),         # nicht gemappt
])
def test_laptop_location(server, expected):
    assert rb.laptop_location(server, MAP) == expected


def test_longest_prefix_wins():
    mp = {"/music": "Y:/Musik", "/music/inbox": "Z:/Highres/_sc-digger-inbox"}
    assert rb.laptop_location("/music/inbox/a.wav", mp) == "file://localhost/Z:/Highres/_sc-digger-inbox/a.wav"
    assert rb.laptop_location("/music/other/a.wav", mp) == "file://localhost/Y:/Musik/other/a.wav"


# ---------------- Inbox einlesen ----------------
def test_scan_inbox_reads_tags_and_skips_rejected_and_temp(tmp_path):
    inbox = tmp_path / "inbox"
    _audio(inbox / "155-160" / "8A" / "A - One.wav", artist="A", title="One", bpm=156.2, key_name="Am",
           genre="Schranz", comment="Score: 87p")
    _audio(inbox / "150-155" / "5A" / "B - Two.mp3", artist="B", title="Two & <Three>", bpm=152)
    _audio(inbox / "_rejected" / "fake.wav")
    _audio(inbox / "_rejected" / "clipped" / "loud.wav")
    (inbox / ".riff-abc.tmp").write_bytes(b"x")
    (inbox / "_dl_xyz").mkdir()
    _audio(inbox / "_dl_xyz" / "half.wav")
    (inbox / "cover.jpg").write_bytes(b"\xff\xd8")

    tracks = rb.scan_inbox(inbox)
    assert [t.path.relative_to(inbox).as_posix() for t in tracks] == ["150-155/5A/B - Two.mp3", "155-160/8A/A - One.wav"]
    one = tracks[1]
    assert (one.title, one.artist, one.genre, one.key, one.comment) == ("One", "A", "Schranz", "Am", "Score: 87p")
    assert one.bpm == 156.0
    assert 2.5 < one.duration_s < 3.5 and one.size == one.path.stat().st_size
    assert tracks[0].title == "Two & <Three>" and tracks[0].genre == "Schranz"


def test_untagged_file_falls_back_to_filename(tmp_path):
    inbox = tmp_path / "inbox"
    _audio(inbox / "Some Artist - Some Title.flac")
    (t,) = rb.scan_inbox(inbox)
    assert t.title == "Some Artist - Some Title" and t.artist == "" and t.bpm is None


# ---------------- XML bauen ----------------
def _tracks(tmp_path):
    inbox = tmp_path / "music" / "inbox"
    a = _audio(inbox / "155-160" / "8A" / "A - One.wav", artist="A", title="One", bpm=156, key_name="Am", genre="Schranz")
    b = _audio(inbox / "150-155" / "5A" / "B - Two.mp3", artist="B", title="Two & <Three>", bpm=152)
    c = _audio(inbox / "C - Old.flac", artist="C", title="Old")
    _added(a, datetime(2026, 9, 28, 8, 0))    # KW 40
    _added(b, datetime(2026, 9, 25, 8, 0))    # KW 39
    _added(c, datetime(2026, 9, 22, 8, 0))    # KW 39
    mp = {str(inbox): "Z:/Highres/_sc-digger-inbox"}
    return rb.scan_inbox(inbox), mp


def test_build_xml_collection(tmp_path):
    tracks, mp = _tracks(tmp_path)
    root = ET.fromstring(rb.build_xml(tracks, mp, now=NOW))
    assert root.tag == "DJ_PLAYLISTS" and root.get("Version") == "1.0.0"
    coll = root.find("COLLECTION")
    assert coll.get("Entries") == "3"
    entries = {t.get("Name"): t for t in coll.findall("TRACK")}
    assert [t.get("TrackID") for t in coll.findall("TRACK")] == ["1", "2", "3"]
    one = entries["One"]
    assert one.get("Artist") == "A" and one.get("Genre") == "Schranz"
    assert one.get("AverageBpm") == "156.00" and one.get("Tonality") == "Am"
    assert one.get("Kind") == "WAV File" and one.get("TotalTime") == "3"
    assert one.get("DateAdded") == "2026-09-28"
    assert one.get("Location") == "file://localhost/Z:/Highres/_sc-digger-inbox/155-160/8A/A%20-%20One.wav"
    assert entries["Two & <Three>"].get("Kind") == "MP3 File"
    assert entries["Old"].get("Kind") == "FLAC File" and entries["Old"].get("AverageBpm") is None


def test_build_xml_playlists(tmp_path):
    tracks, mp = _tracks(tmp_path)
    root = ET.fromstring(rb.build_xml(tracks, mp, now=NOW))
    ids = {t.get("Name"): t.get("TrackID") for t in root.find("COLLECTION").findall("TRACK")}
    top = root.find("PLAYLISTS/NODE")
    assert top.get("Name") == "ROOT" and top.get("Type") == "0" and top.get("Count") == "1"
    folder = top.find("NODE")
    assert folder.get("Name") == "sc-digger" and folder.get("Type") == "0" and folder.get("Count") == "2"
    names = [n.get("Name") for n in folder.findall("NODE")]
    assert names == ["KW 40/2026", "KW 39/2026"]                      # eine Playlist pro Woche, neueste zuerst
    lists = {n.get("Name"): n for n in folder.findall("NODE")}
    for n in lists.values():
        assert n.get("Type") == "1" and n.get("KeyType") == "0"
        assert n.get("Entries") == str(len(n.findall("TRACK")))
    assert [t.get("Key") for t in lists["KW 39/2026"].findall("TRACK")] == [ids["Two & <Three>"], ids["Old"]]
    assert [t.get("Key") for t in lists["KW 40/2026"].findall("TRACK")] == [ids["One"]]


def test_unmapped_tracks_are_left_out(tmp_path):
    tracks, _ = _tracks(tmp_path)
    root = ET.fromstring(rb.build_xml(tracks, {"/somewhere/else": "Z:/x"}, now=NOW))
    assert root.find("COLLECTION").get("Entries") == "0"


# ---------------- Datei schreiben ----------------
def _cfg(tmp_path, **rekordbox) -> Config:
    c = Config.load(Path(__file__).resolve().parents[2] / "config.yaml")
    inbox = tmp_path / "music" / "inbox"
    c.raw["download"] = {**c["download"], "inbox_dir": str(inbox), "collection_dir": str(tmp_path / "music" / "Schranz")}
    c.raw["rekordbox"] = {"xml_enabled": True, "xml_path": str(inbox / "sc-digger.xml"),
                          "path_map": {str(inbox): "Z:/Highres/_sc-digger-inbox"}, **rekordbox}
    return c


def test_write_rekordbox_xml(tmp_path):
    _tracks(tmp_path)
    c = _cfg(tmp_path)
    p = rb.write_rekordbox_xml(c)
    assert p == Path(c["rekordbox"]["xml_path"])
    assert ET.parse(p).getroot().find("COLLECTION").get("Entries") == "3"
    assert sorted(x.name for x in p.parent.iterdir() if x.is_file()) == ["C - Old.flac", "sc-digger.xml"]


def test_write_disabled_or_inside_collection_or_failing_returns_none(tmp_path, monkeypatch):
    _tracks(tmp_path)
    assert rb.write_rekordbox_xml(_cfg(tmp_path, xml_enabled=False)) is None
    inside = tmp_path / "music" / "Schranz" / "x.xml"
    assert rb.write_rekordbox_xml(_cfg(tmp_path, xml_path=str(inside))) is None      # Goldene Regel 4
    assert not inside.exists()
    monkeypatch.setattr(rb, "build_xml", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kaputt")))
    assert rb.write_rekordbox_xml(_cfg(tmp_path)) is None                            # wirft nie


def test_config_defaults():
    import yaml
    raw = yaml.safe_load((Path(__file__).resolve().parents[2] / "config.yaml").read_text(encoding="utf-8"))
    r = raw["rekordbox"]
    assert r["xml_enabled"] is True
    assert r["xml_path"] == "/music/inbox/sc-digger.xml"
    assert r["path_map"] == {"/music/inbox": "Z:/Highres/_sc-digger-inbox"}


# ---------------- Einbindung ----------------
def test_cli_command(monkeypatch):
    calls = []
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg: calls.append(cfg) or Path("/x.xml"))
    monkeypatch.setattr("sys.argv", ["sc_digger.main", "rekordbox"])
    m.cli()
    assert len(calls) == 1


def test_discover_writes_xml_after_delivery_but_not_in_dry_run(tmp_path, monkeypatch):
    class FakeSC:
        def search_tag(self, *a, **k):
            return []

        def user_uploads(self, *a, **k):
            return []

        def reference_activity(self, *a, **k):
            return []

    order = []
    c = _cfg(tmp_path)
    c.raw["state"] = {**c["state"], "db_path": str(tmp_path / "state.sqlite")}
    monkeypatch.setattr(m, "SoundCloudClient", FakeSC)
    monkeypatch.setattr(m, "deliver", lambda *a, **k: order.append("deliver"))
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg: order.append("xml"))
    m._discover(c, dry_run=False, no_telegram=True)
    assert order == ["deliver", "xml"]
    order.clear()
    m._discover(c, dry_run=True, no_telegram=True)
    assert order == ["deliver"]
