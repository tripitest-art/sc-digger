"""Akzeptanztests: Cue-Tag SCDIGGER_FIRST_KICK und POSITION_MARK in der Rekordbox-XML."""
import math
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pytest
from mutagen.id3 import ID3, TXXX

from sc_digger import rekordbox as rb
from sc_digger.cues import FIRST_KICK_TAG, read_first_kick_tag, write_first_kick_tag
from sc_digger.models import Config
from sc_digger.organize import write_tags

ROOT = Path(__file__).resolve().parents[2]
PATH_MAP = {"/music/inbox": "Z:/Inbox"}
FORMATS = [".mp3", ".flac", ".m4a", ".wav", ".aiff"]


def _audio(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ["-b:a", "320k"] if path.suffix == ".mp3" else []
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "anoisesrc=d=1:c=pink:r=44100:a=0.2", "-ac", "2", *args, str(path)],
        check=True,
    )
    return path


def test_tagname():
    assert FIRST_KICK_TAG == "SCDIGGER_FIRST_KICK"


@pytest.mark.parametrize("ext", FORMATS)
def test_schreiben_und_lesen_in_allen_formaten(tmp_path, ext):
    p = _audio(tmp_path / f"t{ext}")
    assert read_first_kick_tag(p) is None
    assert write_first_kick_tag(p, 6.4) is True
    assert read_first_kick_tag(p) == pytest.approx(6.4, abs=0.0005)


@pytest.mark.parametrize("ext", FORMATS)
def test_ueberschreiben_ersetzt_den_wert(tmp_path, ext):
    p = _audio(tmp_path / f"t{ext}")
    assert write_first_kick_tag(p, 6.4)
    assert write_first_kick_tag(p, 12.0)
    assert read_first_kick_tag(p) == pytest.approx(12.0, abs=0.0005)


def test_id3_enthaelt_genau_einen_frame(tmp_path):
    p = _audio(tmp_path / "t.mp3")
    write_first_kick_tag(p, 6.4)
    write_first_kick_tag(p, 12.0)
    frames = [f for f in ID3(str(p)).values() if isinstance(f, TXXX) and f.desc == FIRST_KICK_TAG]
    assert len(frames) == 1 and frames[0].text == ["12.000"]


@pytest.mark.parametrize("ext", FORMATS)
def test_wert_bleibt_nach_write_tags_erhalten(tmp_path, ext):
    p = _audio(tmp_path / f"t{ext}")
    assert write_first_kick_tag(p, 6.4)
    assert write_tags(p, artist="A", title="T", bpm=152.0)
    assert read_first_kick_tag(p) == pytest.approx(6.4, abs=0.0005)


def test_ungueltige_eingaben_liefern_false_und_aendern_nichts(tmp_path):
    assert write_first_kick_tag(tmp_path / "gibt-es-nicht.mp3", 6.4) is False
    p = _audio(tmp_path / "t.mp3")
    for bad in (-1.0, float("nan"), float("inf")):
        assert write_first_kick_tag(p, bad) is False
    assert read_first_kick_tag(p) is None


def test_lesen_ist_robust(tmp_path):
    assert read_first_kick_tag(tmp_path / "gibt-es-nicht.mp3") is None
    kaputt = tmp_path / "kaputt.mp3"
    kaputt.write_bytes(b"kein audio")
    assert read_first_kick_tag(kaputt) is None
    p = _audio(tmp_path / "t.mp3")
    tags = ID3()
    tags.add(TXXX(encoding=3, desc=FIRST_KICK_TAG, text=["abc"]))
    tags.save(str(p))
    assert read_first_kick_tag(p) is None


def _t(name, first_kick_s, root="/music/inbox"):
    return rb.InboxTrack(
        path=Path(root) / "x" / f"{name}.mp3", title=name, artist="A", genre="Hardtechno",
        bpm=152.0, key="5A", comment="", duration_s=300.0, size=1000,
        added=datetime(2026, 10, 5, 12, 0), first_kick_s=first_kick_s,
    )


def _parse(xml: str) -> ET.Element:
    return ET.fromstring(xml.split("\n", 1)[1])


def test_inboxtrack_hat_first_kick_s_mit_standard_none():
    t = rb.InboxTrack(path=Path("/x.mp3"), title="t", artist="a", genre="g", bpm=None, key="",
                      comment="", duration_s=1.0, size=1, added=datetime(2026, 10, 5))
    assert t.first_kick_s is None


def test_scan_inbox_liest_den_tag(tmp_path):
    inbox = tmp_path / "inbox"
    mit = _audio(inbox / "a" / "mit.mp3")
    _audio(inbox / "a" / "ohne.mp3")
    write_first_kick_tag(mit, 6.4)
    by_name = {t.path.name: t for t in rb.scan_inbox(inbox)}
    assert by_name["mit.mp3"].first_kick_s == pytest.approx(6.4, abs=0.0005)
    assert by_name["ohne.mp3"].first_kick_s is None


def test_build_xml_schreibt_position_mark_als_memory_cue():
    xml = rb.build_xml([_t("a", 6.4), _t("b", None), _t("c", 12.34567, root="/anderswo")], PATH_MAP)
    root = _parse(xml)
    tracks = {t.get("Name"): t for t in root.find("COLLECTION")}
    assert set(tracks) == {"a", "b"}  # "c" ist nicht gemappt
    marks = tracks["a"].findall("POSITION_MARK")
    assert len(marks) == 2
    assert dict(marks[0].attrib) == {
        "Name": "First Kick", "Type": "0", "Start": "6.400", "Num": "0",
        "Red": "0", "Green": "255", "Blue": "0",
    }
    assert dict(marks[1].attrib) == {"Name": "First Kick", "Type": "0", "Start": "6.400", "Num": "-1"}
    assert tracks["b"].findall("POSITION_MARK") == []


def test_build_xml_rundet_start_auf_millisekunden():
    root = _parse(rb.build_xml([_t("a", 12.34567)], PATH_MAP))
    assert root.find("COLLECTION/TRACK/POSITION_MARK").get("Start") == "12.346"

def _cfg(tmp_path, **rekordbox_opts):
    inbox = tmp_path / "inbox"
    coll = tmp_path / "coll"
    inbox.mkdir()
    coll.mkdir()
    write_first_kick_tag(_audio(inbox / "150-155" / "5A" / "a.mp3"), 6.4)
    cfg = Config(dict(Config.load(ROOT / "config.yaml").raw))
    cfg.raw["download"] = {**cfg["download"], "inbox_dir": str(inbox), "collection_dir": str(coll)}
    cfg.raw["rekordbox"] = {
        "xml_enabled": True, "xml_path": str(tmp_path / "sc-digger.xml"),
        "path_map": {str(inbox): "Z:/Inbox"}, **rekordbox_opts,
    }
    return cfg


def test_schreiben_mit_cues_enabled_enthaelt_die_marke(tmp_path):
    cfg = _cfg(tmp_path, cues_enabled=True)
    root = ET.parse(rb.write_rekordbox_xml(cfg)).getroot()
    assert root.find("COLLECTION/TRACK/POSITION_MARK").get("Start") == "6.400"


@pytest.mark.parametrize("opts", [{}, {"cues_enabled": False}])
def test_schreiben_ohne_cues_enabled_enthaelt_keine_marke(tmp_path, opts):
    cfg = _cfg(tmp_path, **opts)
    root = ET.parse(rb.write_rekordbox_xml(cfg)).getroot()
    assert root.find("COLLECTION/TRACK") is not None
    assert root.find("COLLECTION/TRACK/POSITION_MARK") is None


def test_config_yaml_schaltet_cues_ein():
    assert Config.load(ROOT / "config.yaml")["rekordbox"]["cues_enabled"] is True
