from pathlib import Path
import sqlite3
import pytest
from starlette.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.database import DigestItem, DigestSearchResult, query_digest


def test_query_digest_missing_databases(tmp_path: Path):
    # Beide DBs fehlen -> leeres Ergebnis, wirft nie
    res = query_digest(tmp_path / "missing_seen.sqlite", tmp_path / "missing_tracks.sqlite")
    assert res.total == 0
    assert res.items == []


def test_query_digest_state_db_only(tmp_path: Path):
    state_db = tmp_path / "seen.sqlite"
    track_db = tmp_path / "missing_tracks.sqlite"

    with sqlite3.connect(state_db) as db:
        db.execute(
            "CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)"
        )
        db.execute(
            "INSERT INTO seen VALUES (1, 'https://soundcloud.com/dj/track', 'DJ - Title', '2026-10-14 12:00:00')"
        )

    res = query_digest(state_db, track_db)
    assert res.total == 1
    item = res.items[0]
    assert item.sc_id == 1
    assert item.artist == "DJ"
    assert item.title == "Title"
    assert item.in_collection is False
    assert item.in_inbox is False
    assert item.download_kind == "stream"
    assert item.feedback is None


def test_query_digest_status_filter_open(tmp_path: Path):
    state_db = tmp_path / "seen.sqlite"
    track_db = tmp_path / "tracks.sqlite"

    with sqlite3.connect(state_db) as db:
        db.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        db.execute("INSERT INTO seen VALUES (10, 'u1', 'A - Track1', '2026-10-10 10:00:00')")
        db.execute("INSERT INTO seen VALUES (20, 'u2', 'B - Track2', '2026-10-11 10:00:00')")

    with sqlite3.connect(track_db) as db:
        db.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT, status TEXT)")
        # Track 10 liegt in der Inbox
        db.execute("INSERT INTO tracks VALUES (1, '/music/inbox/[10] Track1.wav', 'inbox')")

    # Filter 'open' -> nur Track 20
    res_open = query_digest(state_db, track_db, status_filter="open")
    assert res_open.total == 1
    assert res_open.items[0].sc_id == 20

    # Filter 'inbox' -> nur Track 10
    res_inbox = query_digest(state_db, track_db, status_filter="inbox")
    assert res_inbox.total == 1
    assert res_inbox.items[0].sc_id == 10


def test_web_digest_routes(tmp_path: Path):
    state_db = tmp_path / "seen.sqlite"
    track_db = tmp_path / "tracks.sqlite"

    with sqlite3.connect(state_db) as db:
        db.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        db.execute("INSERT INTO seen VALUES (500, 'https://sc.com/test', 'Viper - Moonlight', '2026-10-12 10:00:00')")

    with sqlite3.connect(track_db) as db:
        db.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT, status TEXT)")

    cfg = Config({
        "state": {
            "db_path": str(state_db),
            "track_db_path": str(track_db),
        }
    })

    app = create_app(cfg, password=None, allow_anonymous=True)
    client = TestClient(app)

    # API JSON
    r_api = client.get("/api/digest")
    assert r_api.status_code == 200
    data = r_api.json()
    assert data["total"] == 1
    assert data["items"][0]["sc_id"] == 500
    assert data["items"][0]["title"] == "Moonlight"

    # HTML Page
    r_html = client.get("/digest")
    assert r_html.status_code == 200
    assert "Digest &amp; Entdeckungen" in r_html.text
    assert "Moonlight" in r_html.text
    assert "Viper" in r_html.text

    # Search filter in HTML
    r_search = client.get("/digest?q=NonExistent")
    assert r_search.status_code == 200
    assert "Keine Vorschläge für diese Filtereinstellungen gefunden." in r_search.text


def test_query_digest_fuzzy_matches_archive_tracks(tmp_path: Path):
    state_db = tmp_path / "seen.sqlite"
    track_db = tmp_path / "tracks.sqlite"

    with sqlite3.connect(state_db) as db:
        db.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        db.execute("INSERT INTO seen VALUES (801, 'url801', 'O.B.I. - Hard As Steel (Original Mix)', '2026-10-14 12:00:00')")
        db.execute("INSERT INTO seen VALUES (802, 'url802', 'Unknown - Something Else', '2026-10-14 12:05:00')")

    with sqlite3.connect(track_db) as db:
        db.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT, artist TEXT, title TEXT, status TEXT)")
        # Archiv-Track in der Sammlung (ohne [<sc_id>] im Namen)
        db.execute("INSERT INTO tracks VALUES (1, '/music/Schranz/O.B.I. - Hard As Steel.wav', 'O.B.I.', 'Hard As Steel', 'archive')")

    res = query_digest(state_db, track_db)
    assert res.total == 2
    item801 = next(it for it in res.items if it.sc_id == 801)
    assert item801.in_collection is True
    assert item801.in_inbox is False

    item802 = next(it for it in res.items if it.sc_id == 802)
    assert item802.in_collection is False
    assert item802.in_inbox is False

    # Filter nach status=collection
    res_coll = query_digest(state_db, track_db, status_filter="collection")
    assert res_coll.total == 1
    assert res_coll.items[0].sc_id == 801


def test_query_digest_inbox_dir_ignores_rejected_folders(tmp_path: Path):
    state_db = tmp_path / "seen.sqlite"
    track_db = tmp_path / "tracks.sqlite"
    inbox_dir = tmp_path / "inbox"
    rej = inbox_dir / "_rejected" / "duplicate"
    rej.mkdir(parents=True)
    (rej / "Viper - Fake Track.mp3").write_bytes(b"dummy")

    with sqlite3.connect(state_db) as db:
        db.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        db.execute("INSERT INTO seen VALUES (901, 'url901', 'Viper - Fake Track', '2026-10-14 12:00:00')")

    res = query_digest(state_db, track_db, inbox_dir=inbox_dir)
    assert res.total == 1
    assert res.items[0].in_inbox is False


def test_query_digest_rejected_duplicate_under_hidden_stays_ignored(tmp_path: Path):
    """Issue #301: nur "_rejected/duplicate" direkt unter der Inbox ist erlaubt.

    Liegt der Ordner unter einem versteckten Unterordner (z. B. "150-155/.hidden"),
    bleibt die Datei ausgeschlossen.
    """
    state_db = tmp_path / "seen.sqlite"
    track_db = tmp_path / "tracks.sqlite"
    inbox_dir = tmp_path / "inbox"
    hidden = inbox_dir / "150-155" / ".hidden" / "_rejected" / "duplicate"
    hidden.mkdir(parents=True)
    (hidden / "[555] Zatox - Dopamin.wav").write_bytes(b"dummy")

    with sqlite3.connect(state_db) as db:
        db.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        db.execute("INSERT INTO seen VALUES (555, 'url555', 'Zatox - Dopamin', '2026-10-14 12:00:00')")

    res = query_digest(state_db, track_db, inbox_dir=inbox_dir)
    item = next(it for it in res.items if it.sc_id == 555)
    assert item.in_inbox is False
    assert item.in_collection is False
    assert item.download_kind == "stream"
