import xml.etree.ElementTree as ET
from pathlib import Path

from sc_digger import audit, cues
from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config


def _seed(tmp_path: Path) -> Path:
    audio = tmp_path / "Schranz" / "t.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"\x00" * 100)
    assert cues.write_first_kick_tag(audio, 2.0)
    assert cues.write_drop_tag(audio, 100.0)
    db_file = tmp_path / "t.sqlite"
    with TrackDB(str(db_file)) as db:
        db.upsert_track(TrackRecord(
            path=str(audio), mtime=1700000000.0, size=100, artist="A", title="Banger",
            bpm=150.0, key_camelot="8A", duration=300.0,
            quality_status=QualityStatus.OK, status=TrackStatus.ARCHIVE,
        ))
    return db_file


def _export(tmp_path: Path, **kw) -> ET.Element:
    db_file = _seed(tmp_path)
    out = tmp_path / "out.xml"
    with TrackDB(str(db_file)) as db:
        n = audit.export_collection_xml(db, out, {str(tmp_path): "C:/Music"}, **kw)
    assert n == 1
    return ET.parse(out).getroot().find("COLLECTION").find("TRACK")


def _hot(track: ET.Element) -> dict[str, ET.Element]:
    return {m.get("Num"): m for m in track.findall("POSITION_MARK") if m.get("Num") != "-1"}


def test_standard_8pad_layout_und_dauer(tmp_path):
    track = _export(tmp_path, hot_cue_layout="standard_8pad")
    hot = _hot(track)
    assert hot["0"].get("Name") == "Main Entry" and hot["0"].get("Start") == "48.800"
    assert hot["1"].get("Name") == "Late Entry" and hot["1"].get("Start") == "74.400"
    assert hot["2"].get("Name") == "Drop 1" and hot["2"].get("Start") == "100.000"
    assert hot["7"].get("Name") == "Outro" and hot["7"].get("Start") == "274.400"
    assert track.get("TotalTime") == "300"


def test_standardwerte_behalten_legacy_ohne_anker_und_comment(tmp_path):
    track = _export(tmp_path)
    hot = _hot(track)
    assert hot["0"].get("Name") == "First Kick"
    assert hot["1"].get("Name") == "Drop"
    assert "7" not in hot
    assert track.find("TEMPO") is None
    assert track.get("Comments") == ""


def test_grid_anchor_ist_erstes_kind(tmp_path):
    track = _export(tmp_path, grid_anchor=True)
    first = list(track)[0]
    assert first.tag == "TEMPO"
    assert first.attrib == {"Beginning": "2.000", "Bpm": "150.00", "Metro": "4/4", "Battito": "1"}


def test_kompakter_comment(tmp_path):
    comment = _export(tmp_path, compact_style_comments=True).get("Comments")
    assert comment.startswith("[150|8A|")
    assert "|D1:1:40" in comment and comment.endswith("]")


def test_run_audit_gibt_konfiguration_weiter(tmp_path, monkeypatch):
    db_file = _seed(tmp_path)
    xml_out = tmp_path / "run.xml"
    cfg = Config({
        "download": {"collection_dir": str(tmp_path / "Schranz"), "inbox_dir": str(tmp_path / "inbox")},
        "state": {"track_db_path": str(db_file)},
        "rekordbox": {
            "path_map": {str(tmp_path): "C:/Music"},
            "hot_cue_layout": "standard_8pad",
            "grid_anchor": True,
            "cues_as_hotcues": False,
        },
    })
    monkeypatch.setattr(audit, "audit_collection", lambda collection_dir, cfg, db, *, force=False: audit.AuditSummary())
    monkeypatch.setattr(audit, "fill_fingerprints", lambda db, **kw: (0, 0))
    audit.run_audit(cfg, export_xml_path=xml_out)
    track = ET.parse(xml_out).getroot().find("COLLECTION").find("TRACK")
    assert track.find("TEMPO") is not None
    marks = track.findall("POSITION_MARK")
    assert marks and all(m.get("Num") == "-1" for m in marks)
    assert "Drop 1" in [m.get("Name") for m in marks]
