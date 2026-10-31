"""Tests für den Sammlungs-XML-Export aus der TrackDB (audit --export-xml)."""
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from sc_digger import audit
from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config

PATH_MAP = {"/music/Schranz": "C:/Music/Schranz"}


def _sc_digger_folder(root: ET.Element) -> ET.Element:
    root_node = root.find("PLAYLISTS").find("NODE")
    for node in root_node.findall("NODE"):
        if node.get("Name") == "sc-digger":
            return node
    raise AssertionError("sc-digger-Ordner nicht gefunden")


def _seed(db_path: Path) -> None:
    with TrackDB(str(db_path)) as db:
        db.upsert_track(TrackRecord(
            path="/music/Schranz/152/A.wav", mtime=1700000000.0, size=100,
            artist="A", title="A", bpm=152.0, key_camelot="5A",
            quality_status=QualityStatus.OK, status=TrackStatus.ARCHIVE,
        ))
        db.upsert_track(TrackRecord(
            path="/music/Schranz/155/B.flac", mtime=1700000100.0, size=200,
            artist="B", title="B", bpm=155.0, key_camelot="8A",
            quality_status=QualityStatus.OK, status=TrackStatus.ARCHIVE,
        ))
        db.upsert_track(TrackRecord(
            path="/music/Schranz/C.mp3", mtime=1700000200.0, size=300,
            artist="C", title="C", quality_status=QualityStatus.FAKE_TRANSCODE,
            status=TrackStatus.ARCHIVE,
        ))
        db.upsert_track(TrackRecord(
            path="/music/Schranz/D.wav", mtime=1700000300.0, size=400,
            artist="D", title="D", quality_status=QualityStatus.CORRUPT,
            status=TrackStatus.ARCHIVE,
        ))
        db.upsert_track(TrackRecord(
            path="/music/Schranz/E.wav", mtime=1700000400.0, size=500,
            artist="E", title="E", quality_status=QualityStatus.OK,
            status=TrackStatus.INBOX,
        ))
        db.upsert_track(TrackRecord(
            path="/other/F.wav", mtime=1700000500.0, size=600,
            artist="F", title="F", quality_status=QualityStatus.OK,
            status=TrackStatus.ARCHIVE,
        ))


def test_export_ueberspringt_fake_corrupt_nicht_archive(tmp_path):
    db_file = tmp_path / "t.sqlite"
    _seed(db_file)
    xml_out = tmp_path / "out.xml"
    with TrackDB(str(db_file)) as db:
        count = audit.export_collection_xml(db, xml_out, PATH_MAP)
    # C (fake), D (corrupt), E (inbox) raus, F (nicht gemappt) raus
    assert count == 2
    tree = ET.parse(xml_out)
    names = {t.get("Name") for t in tree.getroot().find("COLLECTION").findall("TRACK")}
    assert names == {"A", "B"}


def test_export_playlists_bpm_und_key(tmp_path):
    db_file = tmp_path / "t.sqlite"
    _seed(db_file)
    xml_out = tmp_path / "out.xml"
    with TrackDB(str(db_file)) as db:
        audit.export_collection_xml(db, xml_out, PATH_MAP)
    root = ET.parse(xml_out).getroot()
    nodes = _sc_digger_folder(root)
    child_names = [n.get("Name") for n in nodes.findall("NODE")]
    assert "BPM" in child_names
    assert "Key" in child_names


def test_export_playlists_versionen(tmp_path):
    db_file = tmp_path / "t.sqlite"
    _seed(db_file)
    xml_out = tmp_path / "out.xml"
    with TrackDB(str(db_file)) as db:
        audit.export_collection_xml(
            db, xml_out, PATH_MAP,
            playlists_by_bpm=False, playlists_by_key=False, playlists_by_version=True,
        )
    root = ET.parse(xml_out).getroot()
    nodes = _sc_digger_folder(root)
    child_names = [n.get("Name") for n in nodes.findall("NODE")]
    assert "Versionen" in child_names
    vers_node = next(n for n in nodes.findall("NODE") if n.get("Name") == "Versionen")
    assert len(vers_node.findall("NODE")) > 0


def test_export_ohne_playlists(tmp_path):
    db_file = tmp_path / "t.sqlite"
    _seed(db_file)
    xml_out = tmp_path / "out.xml"
    with TrackDB(str(db_file)) as db:
        audit.export_collection_xml(
            db, xml_out, PATH_MAP,
            playlists_by_bpm=False, playlists_by_key=False,
        )
    root = ET.parse(xml_out).getroot()
    folder = _sc_digger_folder(root)
    assert folder.get("Count") == "0"
    assert folder.findall("NODE") == []


def test_export_verhindert_schreiben_in_sammlung(tmp_path):
    db_file = tmp_path / "t.sqlite"
    _seed(db_file)
    coll = tmp_path / "Schranz"
    coll.mkdir()
    with TrackDB(str(db_file)) as db:
        with pytest.raises(ValueError, match="Sammlung"):
            audit.export_collection_xml(db, coll / "x.xml", PATH_MAP, collection_dir=coll)


def test_export_xml_atomar_kein_tmp_rest(tmp_path):
    db_file = tmp_path / "t.sqlite"
    _seed(db_file)
    xml_out = tmp_path / "out.xml"
    with TrackDB(str(db_file)) as db:
        audit.export_collection_xml(db, xml_out, PATH_MAP)
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".xml.tmp")]
    assert leftovers == []


def test_run_audit_ruft_export_auf(tmp_path, monkeypatch):
    db_file = tmp_path / "t.sqlite"
    _seed(db_file)
    xml_out = tmp_path / "run.xml"
    cfg = Config({
        "download": {"collection_dir": str(tmp_path / "Schranz"), "inbox_dir": str(tmp_path / "inbox")},
        "state": {"track_db_path": str(db_file)},
        "rekordbox": {"path_map": PATH_MAP},
    })
    called: dict = {}

    def fake_audit_collection(collection_dir, cfg, db, *, force=False):
        return audit.AuditSummary()

    monkeypatch.setattr(audit, "audit_collection", fake_audit_collection)
    monkeypatch.setattr(audit, "fill_fingerprints", lambda db, **kw: (0, 0))

    summary = audit.run_audit(cfg, export_xml_path=xml_out)
    assert summary is not None
    assert xml_out.exists()
    root = ET.parse(xml_out).getroot()
    assert root.find("COLLECTION").get("Entries") == "2"
