"""Akzeptanztests: Digest nach Plays und Likes sortierbar."""
import sqlite3

from starlette.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.database import DIGEST_SORT_FIELDS, query_digest


def _seed(tmp_path):
    """Drei Einträge, deren chronologische Reihenfolge (3, 2, 1) sich von der
    Plays-Reihenfolge (1, 3, 2) und der Likes-Reihenfolge (2, 3, 1) unterscheidet.
    Sonst waere ein nicht implementierter Sortierschluessel versehentlich gruen."""
    state_db = tmp_path / "seen.sqlite"
    track_db = tmp_path / "tracks.sqlite"
    with sqlite3.connect(state_db) as db:
        db.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        db.execute("INSERT INTO seen VALUES (1, 'u1', 'A - Alt', '2026-10-10 10:00:00')")
        db.execute("INSERT INTO seen VALUES (2, 'u2', 'B - Mittel', '2026-10-11 10:00:00')")
        db.execute("INSERT INTO seen VALUES (3, 'u3', 'C - Neu', '2026-10-12 10:00:00')")
    with sqlite3.connect(track_db) as db:
        db.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT, status TEXT)")
        db.execute(
            "CREATE TABLE track_snapshots ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, sc_id INTEGER NOT NULL, artist TEXT NOT NULL,"
            " title TEXT, plays INTEGER NOT NULL DEFAULT 0, likes INTEGER NOT NULL DEFAULT 0,"
            " reposts INTEGER NOT NULL DEFAULT 0, comments INTEGER NOT NULL DEFAULT 0, recorded_at TEXT)"
        )
        for sc_id, plays, likes in ((1, 900, 5), (2, 100, 700), (3, 500, 50)):
            db.execute(
                "INSERT INTO track_snapshots (sc_id, artist, title, plays, likes, recorded_at)"
                " VALUES (?, 'X', 'Y', ?, ?, '2026-10-13T00:00:00Z')",
                (sc_id, plays, likes),
            )
    return state_db, track_db


def test_sortierfelder_enthalten_plays_und_likes():
    assert "plays" in DIGEST_SORT_FIELDS
    assert "likes" in DIGEST_SORT_FIELDS


def test_chronologische_ordnung_ist_der_ausgangspunkt(tmp_path):
    state_db, track_db = _seed(tmp_path)
    res = query_digest(state_db, track_db)
    assert [i.sc_id for i in res.items] == [3, 2, 1]


def test_query_digest_sortiert_nach_plays_absteigend(tmp_path):
    state_db, track_db = _seed(tmp_path)
    res = query_digest(state_db, track_db, sort="plays", descending=True)
    assert [i.sc_id for i in res.items] == [1, 3, 2]


def test_query_digest_sortiert_nach_likes_absteigend(tmp_path):
    state_db, track_db = _seed(tmp_path)
    res = query_digest(state_db, track_db, sort="likes", descending=True)
    assert [i.sc_id for i in res.items] == [2, 3, 1]


def test_digest_seite_bietet_sortierlinks(tmp_path):
    state_db, track_db = _seed(tmp_path)
    cfg = Config({"state": {"db_path": str(state_db), "track_db_path": str(track_db)}})
    client = TestClient(create_app(cfg, password=None, allow_anonymous=True))
    r = client.get("/digest")
    assert r.status_code == 200
    assert "sort=plays" in r.text
    assert "sort=likes" in r.text
