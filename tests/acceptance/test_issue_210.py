"""Akzeptanztests: Sammlungs-XML aus TrackDB exportieren (audit --export-xml)."""
import xml.etree.ElementTree as ET
from pathlib import Path
import pytest

from sc_digger import audit
from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config

PATH_MAP = {"/music/Schranz": "C:/Users/user/Music/Highres/Schranz"}


def _setup_db(db_path: Path) -> None:
    with TrackDB(str(db_path)) as db:
        # 1. Gültiger Archiv-Track
        db.upsert_track(TrackRecord(
            path="/music/Schranz/152/A.wav",
            mtime=1700000000.0,
            size=10000000,
            artist="Artist A",
            title="Track A",
            bpm=152.0,
            key_camelot="5A",
            quality_status=QualityStatus.OK,
            status=TrackStatus.ARCHIVE,
        ))
        # 2. Zweiter Archiv-Track anderer Key/BPM
        db.upsert_track(TrackRecord(
            path="/music/Schranz/155/B.flac",
            mtime=1700000100.0,
            size=12000000,
            artist="Artist B",
            title="Track B",
            bpm=155.0,
            key_camelot="8A",
            quality_status=QualityStatus.OK,
            status=TrackStatus.ARCHIVE,
        ))
        # 3. Fake-Transcode (muss übersprungen werden)
        db.upsert_track(TrackRecord(
            path="/music/Schranz/Fake.mp3",
            mtime=1700000200.0,
            size=5000000,
            artist="Fake",
            title="Fake Track",
            quality_status=QualityStatus.FAKE_TRANSCODE,
            status=TrackStatus.ARCHIVE,
        ))
        # 4. Inbox-Track (nicht archive, muss übersprungen werden)
        db.upsert_track(TrackRecord(
            path="/music/inbox/New.wav",
            mtime=1700000300.0,
            size=8000000,
            artist="New",
            title="New Track",
            quality_status=QualityStatus.OK,
            status=TrackStatus.INBOX,
        ))


def test_export_collection_xml_schreibt_rekordbox_xml(tmp_path):
    db_file = tmp_path / "test_tracks.sqlite"
    _setup_db(db_file)
    xml_out = tmp_path / "sammlung.xml"

    with TrackDB(str(db_file)) as db:
        count = audit.export_collection_xml(db, xml_out, PATH_MAP)

    assert count == 2
    assert xml_out.exists()

    tree = ET.parse(xml_out)
    root = tree.getroot()
    assert root.tag == "DJ_PLAYLISTS"

    coll = root.find("COLLECTION")
    assert coll is not None
    assert coll.get("Entries") == "2"

    tracks = {t.get("Name"): t for t in coll.findall("TRACK")}
    assert "Track A" in tracks
    assert "Track B" in tracks
    assert "Fake Track" not in tracks
    assert "New Track" not in tracks

    # Location-Prüfung
    loc_a = tracks["Track A"].get("Location")
    assert "C:/Users/user/Music/Highres/Schranz/152/A.wav" in loc_a


def test_export_verhindert_schreiben_in_die_sammlung(tmp_path):
    db_file = tmp_path / "test_tracks.sqlite"
    _setup_db(db_file)
    schranz_dir = tmp_path / "Schranz"
    schranz_dir.mkdir()

    with TrackDB(str(db_file)) as db:
        with pytest.raises(ValueError, match="Sammlung"):
            audit.export_collection_xml(
                db,
                schranz_dir / "verboten.xml",
                PATH_MAP,
                collection_dir=schranz_dir,
            )
