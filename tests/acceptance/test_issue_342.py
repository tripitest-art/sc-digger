import xml.etree.ElementTree as ET
from pathlib import Path
import pytest
from sc_digger import audit
from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.rekordbox import InboxTrack

PATH_MAP = {"/fake/archive": "C:/Fake/Archive"}


def test_export_collection_xml_schreibt_position_marks(tmp_path: Path):
    db_file = tmp_path / "test.db"
    xml_out = tmp_path / "collection.xml"

    # Erstelle Fake-Datei mit Cue-Tags oder simuliertem InboxTrack
    track_file = tmp_path / "track.wav"
    track_file.write_bytes(b"RIFF" + b"\x00" * 36)

    with TrackDB(str(db_file)) as db:
        rec = TrackRecord(
            path=str(track_file),
            mtime=1700000000.0,
            size=40,
            artist="DJ Producer",
            title="Schranz Banger",
            bpm=150.0,
            key_camelot="8A",
            quality_status=QualityStatus.OK,
            status=TrackStatus.ARCHIVE,
        )
        db.upsert_track(rec)

        # Mocken von _record_to_inbox_track um Cue-Punkte einzuspeisen
        orig_to_track = audit._record_to_inbox_track

        def mock_record_to_inbox_track(r):
            t = orig_to_track(r)
            t.first_kick_s = 6.4
            t.drop_s = 32.0
            t.breakdown_s = 64.0
            t.breakdown_kind = "silence"
            return t

        audit._record_to_inbox_track = mock_record_to_inbox_track
        try:
            count = audit.export_collection_xml(
                db, xml_out, {str(tmp_path): "C:/Music/Archive"}, hot_cues=True, memory_cues=True
            )
        finally:
            audit._record_to_inbox_track = orig_to_track

    assert count == 1
    assert xml_out.exists()

    tree = ET.parse(xml_out)
    root = tree.getroot()
    track_node = root.find("COLLECTION").find("TRACK")
    assert track_node is not None

    # POSITION_MARK Elemente prüfen
    marks = track_node.findall("POSITION_MARK")
    assert len(marks) > 0

    # Mindestens Hot Cue für First Kick und Drop müssen vorhanden sein
    names = [m.get("Name") for m in marks]
    assert "First Kick" in names
    assert "Drop" in names


def test_record_to_inbox_track_liest_cue_tags(tmp_path: Path):
    from sc_digger import cues

    audio_file = tmp_path / "tagged_track.mp3"
    audio_file.write_bytes(b"\x00" * 100)

    # Schreibe Test-Tags
    cues.write_first_kick_tag(audio_file, 5.0)
    cues.write_drop_tag(audio_file, 25.0)

    rec = TrackRecord(
        path=str(audio_file),
        mtime=1700000000.0,
        size=100,
        artist="Artist",
        title="Title",
        bpm=150.0,
        status=TrackStatus.ARCHIVE,
    )

    inbox_track = audit._record_to_inbox_track(rec)
    assert inbox_track.first_kick_s == pytest.approx(5.0, 0.001)
    assert inbox_track.drop_s == pytest.approx(25.0, 0.001)
