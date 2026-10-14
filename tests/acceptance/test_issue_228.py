from pathlib import Path
import sqlite3
from sc_digger.web.database import DigestItem, DigestSearchResult, query_digest

def test_query_digest_combines_seen_and_track_db(tmp_path: Path):
    state_db = tmp_path / "seen.sqlite"
    track_db = tmp_path / "tracks.sqlite"

    with sqlite3.connect(state_db) as db:
        db.execute(
            "CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)"
        )
        db.execute(
            "INSERT INTO seen VALUES (101, 'https://soundcloud.com/artist1/track1', 'Artist 1 - Track One', '2026-10-10 10:00:00')"
        )
        db.execute(
            "INSERT INTO seen VALUES (102, 'https://soundcloud.com/artist2/track2', 'Artist 2 - Track Two', '2026-10-11 11:00:00')"
        )

    with sqlite3.connect(track_db) as db:
        db.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT, status TEXT)"
        )
        db.execute(
            "CREATE TABLE sc_feedback (sc_id INTEGER PRIMARY KEY, value TEXT, updated_at TEXT)"
        )
        db.execute(
            "CREATE TABLE store_items (sc_id INTEGER PRIMARY KEY, purchase_url TEXT)"
        )
        # Track 101 ist in der Inbox vorhanden und hat Like
        db.execute(
            "INSERT INTO tracks VALUES (1, '/music/inbox/[101] Track One.wav', 'inbox')"
        )
        db.execute("INSERT INTO sc_feedback VALUES (101, 'like', '2026-10-10 12:00:00')")
        # Track 102 ist ein Store-Item
        db.execute("INSERT INTO store_items VALUES (102, 'https://bandcamp.com/buy')")

    res = query_digest(state_db, track_db)
    assert res.total == 2
    # Neueste zuerst (Track 102 vor 101)
    assert res.items[0].sc_id == 102
    assert res.items[0].purchase_url == "https://bandcamp.com/buy"
    assert res.items[0].download_kind == "store"
    assert res.items[0].in_inbox is False

    assert res.items[1].sc_id == 101
    assert res.items[1].feedback == "like"
    assert res.items[1].in_inbox is True

def test_query_digest_filtering(tmp_path: Path):
    state_db = tmp_path / "seen.sqlite"
    track_db = tmp_path / "tracks.sqlite"

    with sqlite3.connect(state_db) as db:
        db.execute(
            "CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)"
        )
        db.execute("INSERT INTO seen VALUES (201, 'url1', 'Alpha - Track', '2026-10-01 10:00:00')")
        db.execute("INSERT INTO seen VALUES (202, 'url2', 'Beta - Sound', '2026-10-02 10:00:00')")

    with sqlite3.connect(track_db) as db:
        db.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT, status TEXT)")
        db.execute("CREATE TABLE sc_feedback (sc_id INTEGER PRIMARY KEY, value TEXT, updated_at TEXT)")
        db.execute("CREATE TABLE store_items (sc_id INTEGER PRIMARY KEY, purchase_url TEXT)")
        db.execute("INSERT INTO sc_feedback VALUES (201, 'like', '2026-10-01 12:00:00')")

    # Filter nach Suchbegriff
    res_search = query_digest(state_db, track_db, search="Beta")
    assert res_search.total == 1
    assert res_search.items[0].sc_id == 202

    # Filter nach Feedback
    res_fb = query_digest(state_db, track_db, feedback="like")
    assert res_fb.total == 1
    assert res_fb.items[0].sc_id == 201
