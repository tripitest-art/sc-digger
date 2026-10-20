"""Eigene Tests für die dauerhafte Sammlungsverknüpfung per Fingerprint (Issue #324).

Ergänzt die Akzeptanztests um Grenzfälle: vorhandene source_url der Archivzeile,
Inbox-Datei ohne [<id>]-Präfix, sc_id aus dem TrackRecord statt aus dem Dateinamen,
Idempotenz und die sc_id einer Inbox-Zeile in query_digest. Netzwerk wird nicht benötigt.
"""
from __future__ import annotations

import sqlite3

from sc_digger.audit import mark_adopted_tracks
from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.fingerprint import encode_fingerprint
from sc_digger.web.database import query_digest

FP_A = [100 + i for i in range(200)]


def _seen_db(tmp_path, sc_id, title="Artist - Track"):
    state_db = tmp_path / "seen.sqlite"
    with sqlite3.connect(state_db) as db:
        db.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        db.execute("INSERT INTO seen VALUES (?, 'u', ?, '2026-10-14 06:00:00')", (sc_id, title))
    return state_db


def _pair(db: TrackDB, tmp_path, *, inbox_name: str, archive_name: str,
          inbox_sc_id: int | None = None, inbox_url: str | None = None,
          archive_url: str | None = None) -> tuple[TrackRecord, TrackRecord]:
    inbox_rec = db.upsert_track(TrackRecord(
        path=(tmp_path / "inb" / inbox_name).as_posix(), mtime=1.0, size=1000,
        artist="Artist", title="Track", sc_id=inbox_sc_id, source_url=inbox_url,
        status=TrackStatus.INBOX,
    ))
    db.set_fingerprint(inbox_rec.id, encode_fingerprint(FP_A), 240.0)
    archive_rec = db.upsert_track(TrackRecord(
        path=(tmp_path / "coll" / archive_name).as_posix(), mtime=2.0, size=1000,
        artist="Artist", title="Track", source_url=archive_url, status=TrackStatus.ARCHIVE,
    ))
    db.set_fingerprint(archive_rec.id, encode_fingerprint(FP_A), 240.0)
    return inbox_rec, archive_rec


def test_vorhandene_source_url_der_archivzeile_bleibt(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        _, archive_rec = _pair(
            db, tmp_path, inbox_name="[99999] Artist - Track.wav",
            archive_name="Renamed.wav", inbox_url="https://soundcloud.com/artist/track",
            archive_url="https://soundcloud.com/artist/original",
        )
        assert len(mark_adopted_tracks(db, now="2026-10-16 12:00:00")) == 1
        archive = db.get_track_by_id(archive_rec.id)
        assert archive.sc_id == 99999
        assert archive.source_url == "https://soundcloud.com/artist/original"


def test_inbox_ohne_praefix_und_ohne_sc_id_uebertraegt_nichts(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        _, archive_rec = _pair(
            db, tmp_path, inbox_name="Artist - Track.wav", archive_name="Renamed.wav",
        )
        assert len(mark_adopted_tracks(db, now="2026-10-16 12:00:00")) == 1
        archive = db.get_track_by_id(archive_rec.id)
        assert archive.sc_id is None
        assert archive.source_url is None


def test_sc_id_aus_dem_record_wird_uebertragen(tmp_path):
    """Steht keine ID im Dateinamen, zählt die bereits gespeicherte sc_id des Records."""
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        _, archive_rec = _pair(
            db, tmp_path, inbox_name="Artist - Track.wav", archive_name="Renamed.wav",
            inbox_sc_id=4711, inbox_url="https://soundcloud.com/artist/track",
        )
        assert len(mark_adopted_tracks(db, now="2026-10-16 12:00:00")) == 1
        archive = db.get_track_by_id(archive_rec.id)
        assert archive.sc_id == 4711
        assert archive.source_url == "https://soundcloud.com/artist/track"


def test_zweiter_lauf_ist_idempotent(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        _, archive_rec = _pair(
            db, tmp_path, inbox_name="[99999] Artist - Track.wav", archive_name="Renamed.wav",
        )
        assert len(mark_adopted_tracks(db, now="2026-10-16 12:00:00")) == 1
        assert mark_adopted_tracks(db, now="2026-10-17 12:00:00") == []
        archive = db.get_track_by_id(archive_rec.id)
        assert archive.sc_id == 99999


def test_query_digest_erkennt_inbox_zeile_ueber_sc_id(tmp_path):
    track_db = tmp_path / "tracks.sqlite"
    with TrackDB(track_db) as db:
        db.upsert_track(TrackRecord(
            path=(tmp_path / "inb" / "Ganz_Anders.wav").as_posix(),
            mtime=1.0, size=1000, artist="Voellig", title="Anderer Name",
            sc_id=55556, status=TrackStatus.INBOX,
        ))
    state_db = _seen_db(tmp_path, 55556, "Artist - Original Title")
    item = query_digest(state_db, track_db, inbox_dir=tmp_path / "inb").items[0]
    assert item.in_inbox is True
    assert item.in_collection is False


def test_praefix_hat_vorrang_vor_gespeicherter_sc_id(tmp_path):
    """Weichen Dateiname und Record auseinander, gilt die ID aus dem Dateinamen."""
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        _, archive_rec = _pair(
            db, tmp_path, inbox_name="[99999] Artist - Track.wav", archive_name="Renamed.wav",
            inbox_sc_id=4711,
        )
        assert len(mark_adopted_tracks(db, now="2026-10-16 12:00:00")) == 1
        assert db.get_track_by_id(archive_rec.id).sc_id == 99999


def test_mark_adopted_unbekannte_id_laesst_archivzeile_unberuehrt(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        archive_rec = db.upsert_track(TrackRecord(
            path="/coll/a.wav", mtime=1.0, size=1, status=TrackStatus.ARCHIVE,
        ))
        assert db.mark_adopted(999999, "2026-10-16 12:00:00", archive_track_id=archive_rec.id) is False
        archive = db.get_track_by_id(archive_rec.id)
        assert archive.sc_id is None
        assert archive.source_url is None
