"""Unit-Tests: Beatgrid-Anker <TEMPO> im Rekordbox-XML-Export (Issue #267).

Der Anker verhindert, dass Rekordbox' Transienten-Erkennung bei 150+ BPM das Raster um
ein 16tel versetzt: Takt 1/Beat 1 sitzt exakt auf first_kick_s.
"""

import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from sc_digger import rekordbox as rb

PATH_MAP = {"/test/inbox": "Z:/Inbox"}


def _track(name: str = "t1", first_kick_s: float | None = 0.045,
           bpm: float | None = 155.0, path: Path | None = None) -> rb.InboxTrack:
    if path is None:
        path = Path("/test/inbox") / "155" / "5A" / f"{name}.wav"
    return rb.InboxTrack(
        path=path, title=name, artist="Artist", genre="Schranz", bpm=bpm, key="5A",
        comment="", duration_s=300.0, size=1000,
        added=datetime(2026, 10, 14, 12, 0), first_kick_s=first_kick_s,
    )


def _parse(xml_str: str) -> ET.Element:
    return ET.fromstring(xml_str.split("\n", 1)[1])


def _track_node(xml_str: str) -> ET.Element:
    root = _parse(xml_str)
    node = root.find("COLLECTION/TRACK")
    assert node is not None
    return node


# ---------------------------------------------------------------- build_xml direkt


def test_tempo_anteil_und_attributwerte():
    node = _track_node(rb.build_xml([_track()], PATH_MAP))
    tempo = node.find("TEMPO")
    assert tempo is not None
    assert tempo.attrib == {
        "Beginning": "0.045", "Bpm": "155.00", "Metro": "4/4", "Battito": "1",
    }


def test_tempo_ist_erstes_kind_vor_den_marks():
    node = _track_node(rb.build_xml([_track()], PATH_MAP))
    assert [child.tag for child in node][0] == "TEMPO"


def test_tempo_steht_vor_position_marks():
    node = _track_node(rb.build_xml([_track()], PATH_MAP, hot_cues=True, memory_cues=True))
    tags = [child.tag for child in node]
    assert tags.index("TEMPO") < tags.index("POSITION_MARK")


def test_tempo_steht_vor_marks_nur_mit_memory_cues():
    node = _track_node(rb.build_xml([_track()], PATH_MAP, hot_cues=False, memory_cues=True))
    tags = [child.tag for child in node]
    assert tags.index("TEMPO") < tags.index("POSITION_MARK")


def test_tempo_ohne_cues_marks():
    node = _track_node(rb.build_xml([_track()], PATH_MAP, hot_cues=False, memory_cues=False))
    assert node.find("TEMPO") is not None
    assert node.findall("POSITION_MARK") == []


def test_beginning_und_bpm_werden_gerundet():
    node = _track_node(rb.build_xml([_track(first_kick_s=12.34567, bpm=133.4)], PATH_MAP))
    tempo = node.find("TEMPO")
    assert tempo.get("Beginning") == "12.346"
    assert tempo.get("Bpm") == "133.40"


def test_beginning_null_ist_erlaubt():
    node = _track_node(rb.build_xml([_track(first_kick_s=0.0, bpm=150.0)], PATH_MAP))
    assert node.find("TEMPO").get("Beginning") == "0.000"


def test_grid_anchor_false_unterdrueckt_tempo():
    node = _track_node(rb.build_xml([_track()], PATH_MAP, grid_anchor=False))
    assert node.find("TEMPO") is None


def test_fehlender_kick_unterdrueckt_tempo():
    node = _track_node(rb.build_xml([_track(first_kick_s=None)], PATH_MAP))
    assert node.find("TEMPO") is None


def test_fehlende_bpm_unterdrueckt_tempo():
    node = _track_node(rb.build_xml([_track(bpm=None)], PATH_MAP))
    assert node.find("TEMPO") is None


def test_negativer_kick_unterdrueckt_tempo():
    node = _track_node(rb.build_xml([_track(first_kick_s=-0.5)], PATH_MAP))
    assert node.find("TEMPO") is None


def test_bpm_null_oder_negativ_unterdrueckt_tempo():
    xml = rb.build_xml([_track("null", bpm=0.0), _track("negativ", bpm=-1.0)], PATH_MAP)
    tracks = _parse(xml).findall("COLLECTION/TRACK")
    assert len(tracks) == 2
    assert [t.find("TEMPO") for t in tracks] == [None, None]


def test_nicht_gemappter_track_ohne_tempo():
    xml = rb.build_xml([_track(path=Path("/anderswo/t1.wav"))], PATH_MAP)
    root = _parse(xml)
    assert root.find("COLLECTION").get("Entries") == "0"
    assert root.find("COLLECTION/TRACK") is None


def test_mehrere_tracks_bekommen_jeden_eigenen_anker():
    xml = rb.build_xml(
        [_track("a", first_kick_s=0.045, bpm=155.0), _track("b", first_kick_s=1.5, bpm=160.0)],
        PATH_MAP,
    )
    tracks = _parse(xml).findall("COLLECTION/TRACK")
    assert tracks[0].find("TEMPO").get("Beginning") == "0.045"
    assert tracks[0].find("TEMPO").get("Bpm") == "155.00"
    assert tracks[1].find("TEMPO").get("Beginning") == "1.500"
    assert tracks[1].find("TEMPO").get("Bpm") == "160.00"


# ------------------------------------------------------ write_rekordbox_xml (Config)


def _run_xml(tmp_path, monkeypatch, **rb_opts):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    mp3 = inbox / "[1] t.mp3"
    mp3.write_bytes(b"\x00")
    monkeypatch.setattr(rb, "scan_inbox", lambda _inbox: [_track(path=mp3)])

    xml_path = tmp_path / "out.xml"
    cfg = {
        "rekordbox": {
            "xml_enabled": True,
            "xml_path": str(xml_path),
            "path_map": {str(inbox): "Z:/Inbox"},
            **rb_opts,
        },
        "download": {"inbox_dir": str(inbox)},
    }
    assert rb.write_rekordbox_xml(cfg) == xml_path
    return _parse(xml_path.read_text(encoding="utf-8"))


def test_config_ohne_schluessel_ist_standard_aktiv(tmp_path, monkeypatch):
    root = _run_xml(tmp_path, monkeypatch, cues_enabled=True)
    assert root.find("COLLECTION/TRACK/TEMPO") is not None


def test_config_wert_none_gilt_als_standard(tmp_path, monkeypatch):
    root = _run_xml(tmp_path, monkeypatch, cues_enabled=True, grid_anchor=None)
    assert root.find("COLLECTION/TRACK/TEMPO") is not None


def test_config_false_unterdrueckt_tempo(tmp_path, monkeypatch):
    root = _run_xml(tmp_path, monkeypatch, cues_enabled=True, grid_anchor=False)
    assert root.find("COLLECTION/TRACK/TEMPO") is None


def test_config_true_schreibt_tempo_neben_den_marks(tmp_path, monkeypatch):
    root = _run_xml(tmp_path, monkeypatch, cues_enabled=True, grid_anchor=True)
    assert root.find("COLLECTION/TRACK/TEMPO") is not None
    assert [m.get("Num") for m in root.findall("COLLECTION/TRACK/POSITION_MARK")] == ["0", "-1"]


def test_cues_enabled_false_schreibt_tempo_ohne_marks(tmp_path, monkeypatch):
    """Der Beatgrid-Anker hängt nicht am Cue-Export (Akzeptanztest aus #267)."""
    root = _run_xml(tmp_path, monkeypatch, cues_enabled=False)
    assert root.find("COLLECTION/TRACK/TEMPO") is not None
    assert root.findall("COLLECTION/TRACK/POSITION_MARK") == []


def test_cues_enabled_false_und_grid_anchor_false_bleibt_ohne_tempo(tmp_path, monkeypatch):
    root = _run_xml(tmp_path, monkeypatch, cues_enabled=False, grid_anchor=False)
    assert root.find("COLLECTION/TRACK/TEMPO") is None
    assert root.findall("COLLECTION/TRACK/POSITION_MARK") == []


# ------------------------------------------------------------------- Schema/Config


def test_schema_feld_ist_bool():
    from sc_digger import schema

    felder = {f.path: f for f in schema.FIELDS}
    assert "rekordbox.grid_anchor" in felder
    assert felder["rekordbox.grid_anchor"].kind == "bool"


def test_config_yaml_aktiviert_den_anker():
    from sc_digger.models import Config
    from sc_digger.schema import validate_config

    root_dir = Path(__file__).resolve().parents[1]
    cfg = Config.load(root_dir / "config.yaml")
    assert cfg.raw["rekordbox"]["grid_anchor"] is True
    assert validate_config(cfg.raw) == []
