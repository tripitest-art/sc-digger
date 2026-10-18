"""Akzeptanztests: Übernommen-Erkennung per Fingerprint verknüpft Sammlungsdatei mit SoundCloud-ID."""
import sqlite3

from sc_digger.audit import mark_adopted_tracks
from sc_digger.db import MIGRATIONS, TrackDB, TrackRecord, TrackStatus
from sc_digger.fingerprint import encode_fingerprint
from sc_digger.web.database import query_digest

FP_A = [100 + i for i in range(200)]


def _seen_db(tmp_path, sc_id, title):
    state_db = tmp_path / "seen.sqlite"
    with sqlite3.connect(state_db) as db:
        db.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        db.execute("INSERT INTO seen VALUES (?, 'u', ?, '2026-10-14 06:00:00')", (sc_id, title))
    return state_db


def test_migration_adds_sc_id_column(tmp_path):
    assert any(name.endswith("tracks_sc_id") for _, name, _ in MIGRATIONS)
    assert len({version for version, _, _ in MIGRATIONS}) == len(MIGRATIONS)
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        cols = {row["name"]: row["type"] for row in db.db.execute("PRAGMA table_info(tracks)").fetchall()}
        assert cols["sc_id"].upper() == "INTEGER"
        rec = db.upsert_track(TrackRecord(path=str(tmp_path / "a.wav"), mtime=1.0, size=1, sc_id=42))
        assert db.get_track_by_id(rec.id).sc_id == 42


def test_mark_adopted_transfers_sc_id_and_url_to_archive(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        inbox_rec = db.upsert_track(TrackRecord(
            path=(tmp_path / "inb" / "[99999] Artist - Track.wav").as_posix(),
            mtime=1.0, size=1000, artist="Artist", title="Track",
            source_url="https://soundcloud.com/artist/track", status=TrackStatus.INBOX,
        ))
        db.set_fingerprint(inbox_rec.id, encode_fingerprint(FP_A), 240.0)
        archive_rec = db.upsert_track(TrackRecord(
            path=(tmp_path / "coll" / "Renamed_Track_No_ID.wav").as_posix(),
            mtime=2.0, size=1000, artist="Artist", title="Track", status=TrackStatus.ARCHIVE,
        ))
        db.set_fingerprint(archive_rec.id, encode_fingerprint(FP_A), 240.0)

        assert len(mark_adopted_tracks(db, now="2026-10-16 12:00:00")) == 1

        archive = db.get_track_by_id(archive_rec.id)
        assert archive.sc_id == 99999
        assert archive.source_url == "https://soundcloud.com/artist/track"
        assert db.get_track_by_id(inbox_rec.id).adopted_at == "2026-10-16 12:00:00"


def test_mark_adopted_without_archive_track_keeps_the_old_behaviour(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        inbox_rec = db.upsert_track(TrackRecord(
            path=(tmp_path / "inb" / "[1] A - B.wav").as_posix(), mtime=1.0, size=1,
            status=TrackStatus.INBOX,
        ))
        assert db.mark_adopted(inbox_rec.id, "2026-10-16 12:00:00") is True
        assert db.get_track_by_id(inbox_rec.id).adopted_at == "2026-10-16 12:00:00"
        assert db.mark_adopted(999999, "2026-10-16 12:00:00", archive_track_id=1) is False


def test_query_digest_detects_adopted_archive_file_by_sc_id_without_fuzzy_match(tmp_path):
    track_db = tmp_path / "tracks.sqlite"
    with TrackDB(track_db) as db:
        db.upsert_track(TrackRecord(
            path=(tmp_path / "coll" / "Completely_Different_Name.wav").as_posix(),
            mtime=1.0, size=1000, artist="Voellig", title="Anderer Name",
            sc_id=55555, status=TrackStatus.ARCHIVE,
        ))
    state_db = _seen_db(tmp_path, 55555, "Artist - Original Title")
    item = query_digest(state_db, track_db, inbox_dir=tmp_path / "inb").items[0]
    assert item.sc_id == 55555
    assert item.in_collection is True
    assert item.in_inbox is False


def test_query_digest_without_sc_id_column_still_works(tmp_path):
    track_db = tmp_path / "alt.sqlite"
    with sqlite3.connect(track_db) as db:
        db.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT, artist TEXT, title TEXT, status TEXT)")
        db.execute("INSERT INTO tracks VALUES (1, '/coll/[7] A - B.wav', 'A', 'B', 'archive')")
    state_db = _seen_db(tmp_path, 7, "Irgendwas")
    assert query_digest(state_db, track_db).items[0].in_collection is True
