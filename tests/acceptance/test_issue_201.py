"""Akzeptanztests: Rekordbox-Export lesend einlesen (Rating, Abspielzähler, BPM, Key, Cues)."""
import json
import xml.etree.ElementTree as ET
from pathlib import Path
import pytest

from sc_digger import rekordbox
from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.rekordbox import (
    RekordboxImportSummary,
    RekordboxTrackData,
    import_rekordbox_xml,
    parse_rekordbox_xml,
)
from sc_digger.schema import validate_config

PATH_MAP = {
    "/music/inbox": "Z:/Highres/_sc-digger-inbox",
    "/music/Schranz": "C:/Users/user/Music/Highres/Schranz",
}

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.0.0" Company="Pioneer DJ"/>
  <COLLECTION Entries="7">
    <TRACK TrackID="1" Name="Track 1" Artist="Artist A" AverageBpm="150.00" Tonality="8A"
           Rating="255" PlayCount="5"
           Location="file://localhost/Z:/Highres/_sc-digger-inbox/150/8A/Track%201.wav">
      <POSITION_MARK Name="First Kick" Type="0" Start="1.234" Num="0" Red="0" Green="255" Blue="0"/>
      <POSITION_MARK Name="First Kick" Type="0" Start="1.234" Num="-1"/>
    </TRACK>
    <TRACK TrackID="2" Name="Track 2" Artist="Artist B" AverageBpm="152.00" Tonality="5A"
           Rating="204" PlayCount="2"
           Location="file://localhost/C:/Users/user/Music/Highres/Schranz/Track%202.flac"/>
    <TRACK TrackID="3" Name="Track 3" Artist="Artist C" AverageBpm="154.00" Tonality="9A"
           Rating="153" PlayCount="1"
           Location="file://localhost/Z:/Highres/_sc-digger-inbox/154/9A/Track%203.mp3"/>
    <TRACK TrackID="4" Name="Track 4" Artist="Artist D"
           Rating="102" PlayCount="0"
           Location="file://localhost/Z:/Highres/_sc-digger-inbox/150/1A/Track%204.wav"/>
    <TRACK TrackID="5" Name="Track 5" Artist="Artist E"
           Rating="51"
           Location="file://localhost/Z:/Highres/_sc-digger-inbox/150/2A/Track%205.wav"/>
    <TRACK TrackID="6" Name="Track 6" Artist="Artist F"
           Rating="0"
           Location="file://localhost/Z:/Highres/_sc-digger-inbox/150/3A/Track%206.wav"/>
    <TRACK TrackID="7" Name="Track 7" Artist="Artist G"
           Location="file://localhost/Z:/Highres/_sc-digger-inbox/150/4A/Unknown.wav"/>
  </COLLECTION>
</DJ_PLAYLISTS>
"""


def test_parse_rekordbox_xml_ratings_and_metadata():
    tracks = parse_rekordbox_xml(SAMPLE_XML, PATH_MAP)
    assert len(tracks) == 7

    # Track 1: 5 Sterne, 5 Plays, Cues
    t1 = tracks[0]
    assert t1.name == "Track 1"
    assert t1.artist == "Artist A"
    assert t1.rating == 5
    assert t1.play_count == 5
    assert t1.bpm == 150.0
    assert t1.key == "8A"
    assert t1.local_path == "/music/inbox/150/8A/Track 1.wav"
    assert len(t1.cues) == 2
    assert t1.cues[0]["name"] == "First Kick"
    assert t1.cues[0]["num"] == 0
    assert t1.cues[0]["color"] == {"red": 0, "green": 255, "blue": 0}
    assert t1.cues[1]["num"] == -1

    # Track 2: 4 Sterne, 2 Plays
    t2 = tracks[1]
    assert t2.rating == 4
    assert t2.play_count == 2
    assert t2.local_path == "/music/Schranz/Track 2.flac"

    # Track 3: 3 Sterne, 1 Play
    assert tracks[2].rating == 3
    assert tracks[2].play_count == 1

    # Track 4: 2 Sterne, 0 Plays
    assert tracks[3].rating == 2
    assert tracks[3].play_count == 0

    # Track 5: 1 Stern, kein PlayCount-Attribut
    assert tracks[4].rating == 1
    assert tracks[4].play_count is None

    # Track 6: 0 Sterne
    assert tracks[5].rating == 0

    # Track 7: Kein Rating, kein PlayCount
    assert tracks[6].rating is None
    assert tracks[6].play_count is None
    assert tracks[6].bpm is None
    assert tracks[6].key is None
    assert tracks[6].cues == []


def test_parse_rekordbox_xml_reverse_path_mapping():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <DJ_PLAYLISTS Version="1.0.0">
      <COLLECTION Entries="1">
        <TRACK TrackID="1" Name="Test" Location="file://localhost/Z:/Highres/_sc-digger-inbox/Sub/Track%20A.wav"/>
      </COLLECTION>
    </DJ_PLAYLISTS>"""
    tracks = parse_rekordbox_xml(xml, PATH_MAP)
    assert len(tracks) == 1
    assert tracks[0].local_path == "/music/inbox/Sub/Track A.wav"


def test_trackdb_migration_und_update_rekordbox_metadata(tmp_path):
    db_file = tmp_path / "tracks.sqlite"
    with TrackDB(db_file) as db:
        rec = db.upsert_track(TrackRecord(
            path="/music/inbox/150/8A/Track 1.wav",
            mtime=1700000000.0,
            size=1000,
            artist="Artist A",
            title="Track 1",
            status=TrackStatus.INBOX,
        ))
        assert rec.rb_rating is None
        assert rec.rb_play_count is None
        assert rec.rb_bpm is None
        assert rec.rb_key is None
        assert rec.rb_cues is None

        cues_json = json.dumps([{"name": "Kick", "type": 0, "start": 1.23, "num": 0}])
        ok = db.update_rekordbox_metadata(
            track_id=rec.id,
            rb_rating=4,
            rb_play_count=3,
            rb_bpm=150.0,
            rb_key="8A",
            rb_cues=cues_json,
            auto_like=True,
        )
        assert ok is True

        updated = db.get_track_by_id(rec.id)
        assert updated is not None
        assert updated.rb_rating == 4
        assert updated.rb_play_count == 3
        assert updated.rb_bpm == 150.0
        assert updated.rb_key == "8A"
        assert updated.rb_cues == cues_json
        # auto_like greift bei rating >= 4 und bisherigem feedback=None:
        assert updated.feedback == "like"


def test_trackdb_auto_like_respektiert_bestehendes_feedback(tmp_path):
    db_file = tmp_path / "tracks.sqlite"
    with TrackDB(db_file) as db:
        # 1. dislike bleibt erhalten
        rec1 = db.upsert_track(TrackRecord(
            path="/music/inbox/Disliked.wav",
            mtime=1700000000.0,
            size=1000,
        ))
        db.set_feedback(rec1.id, "dislike")
        db.update_rekordbox_metadata(rec1.id, rb_rating=5, rb_play_count=10, auto_like=True)
        assert db.get_track_by_id(rec1.id).feedback == "dislike"

        # 2. later bleibt erhalten
        rec2 = db.upsert_track(TrackRecord(
            path="/music/inbox/Later.wav",
            mtime=1700000000.0,
            size=1000,
        ))
        db.set_feedback(rec2.id, "later")
        db.update_rekordbox_metadata(rec2.id, rb_rating=5, rb_play_count=10, auto_like=True)
        assert db.get_track_by_id(rec2.id).feedback == "later"

        # 3. auto_like=False setzt kein like
        rec3 = db.upsert_track(TrackRecord(
            path="/music/inbox/NoAuto.wav",
            mtime=1700000000.0,
            size=1000,
        ))
        db.update_rekordbox_metadata(rec3.id, rb_rating=5, rb_play_count=10, auto_like=False)
        assert db.get_track_by_id(rec3.id).feedback is None


def test_import_rekordbox_xml_matching_und_summary(tmp_path):
    db_file = tmp_path / "tracks.sqlite"
    xml_file = tmp_path / "rekordbox_export.xml"
    xml_file.write_text(SAMPLE_XML, encoding="utf-8")

    with TrackDB(db_file) as db:
        # Track 1: Direkter Pfadmatch (/music/inbox/150/8A/Track 1.wav)
        r1 = db.upsert_track(TrackRecord(
            path="/music/inbox/150/8A/Track 1.wav",
            mtime=1700000000.0,
            size=10000,
            artist="Artist A",
            title="Track 1",
            status=TrackStatus.INBOX,
        ))
        # Track 2: Fallback über Dateinamen ("Track 2.flac"), anderer Ordner in DB
        r2 = db.upsert_track(TrackRecord(
            path="/music/Schranz/DifferentFolder/Track 2.flac",
            mtime=1700000000.0,
            size=10000,
            artist="Artist B",
            title="Track 2",
            status=TrackStatus.ARCHIVE,
        ))
        db.set_feedback(r2.id, "dislike")
        # Track 3: Direkter Pfadmatch, Rating 3 / Plays 1 -> kein Auto-Like
        r3 = db.upsert_track(TrackRecord(
            path="/music/inbox/154/9A/Track 3.mp3",
            mtime=1700000000.0,
            size=10000,
            artist="Artist C",
            title="Track 3",
            status=TrackStatus.INBOX,
        ))

        summary = import_rekordbox_xml(db, xml_file, PATH_MAP, auto_like=True)

        assert summary.total_tracks == 7
        assert summary.matched == 3
        assert summary.unmatched == 4
        assert summary.ratings_count == 3
        assert summary.played_count == 3
        assert summary.auto_likes_count == 1  # nur Track 1 (Track 2 war dislike)

        # Prüfe DB-Inhalte
        t1 = db.get_track_by_id(r1.id)
        assert t1.rb_rating == 5
        assert t1.rb_play_count == 5
        assert t1.feedback == "like"

        t2 = db.get_track_by_id(r2.id)
        assert t2.rb_rating == 4
        assert t2.rb_play_count == 2
        assert t2.feedback == "dislike"

        t3 = db.get_track_by_id(r3.id)
        assert t3.rb_rating == 3
        assert t3.rb_play_count == 1
        assert t3.feedback is None


def test_import_rekordbox_xml_idempotenz(tmp_path):
    db_file = tmp_path / "tracks.sqlite"
    xml_file = tmp_path / "rekordbox_export.xml"
    xml_file.write_text(SAMPLE_XML, encoding="utf-8")

    with TrackDB(db_file) as db:
        db.upsert_track(TrackRecord(
            path="/music/inbox/150/8A/Track 1.wav",
            mtime=1700000000.0,
            size=10000,
            status=TrackStatus.INBOX,
        ))

        # 1. Lauf
        s1 = import_rekordbox_xml(db, xml_file, PATH_MAP, auto_like=True)
        assert s1.matched == 1
        assert s1.auto_likes_count == 1

        # 2. Lauf (gleiche XML)
        s2 = import_rekordbox_xml(db, xml_file, PATH_MAP, auto_like=True)
        assert s2.matched == 1
        # Kein erneuter Auto-Like, da bereits feedback='like' gesetzt ist
        assert s2.auto_likes_count == 0


def test_sammlung_bleibt_vollstaendig_unveraendert_goldene_regel_4(tmp_path):
    schranz_dir = tmp_path / "Schranz"
    schranz_dir.mkdir()
    audio = schranz_dir / "Test.wav"
    audio.write_bytes(b"RIFF....WAVEfmt ")
    mtime_before = audio.stat().st_mtime
    content_before = audio.read_bytes()

    db_file = tmp_path / "tracks.sqlite"
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(SAMPLE_XML, encoding="utf-8")

    with TrackDB(db_file) as db:
        db.upsert_track(TrackRecord(
            path=str(audio).replace("\\", "/"),
            mtime=mtime_before,
            size=len(content_before),
            status=TrackStatus.ARCHIVE,
        ))
        import_rekordbox_xml(db, xml_file, PATH_MAP)

    assert audio.read_bytes() == content_before
    assert audio.stat().st_mtime == mtime_before


def test_config_schema_rekordbox_import_xml_path():
    cfg = {
        "rekordbox": {
            "import_xml_path": "/syncthing/rekordbox_export.xml",
        }
    }
    warnings = validate_config(cfg)
    assert not any("rekordbox.import_xml_path" in w for w in warnings)
