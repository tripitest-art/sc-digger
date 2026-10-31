"""Akzeptanztest: Künstler wird in seen/rejected gespeichert und im Digest/in der Quarantäne angezeigt."""
import sqlite3
from pathlib import Path

from sc_digger.models import Track
from sc_digger.output import State
from sc_digger.web.database import query_digest, query_rejected_tracks


def _track(sc_id: int, title: str, artist: str) -> Track:
    return Track(
        id=sc_id, title=title, url=f"https://soundcloud.com/u/{sc_id}", artist=artist,
        artist_url="", created_at="2026-10-27T06:00:00Z", duration_ms=300000, genre="",
        tags=[], description="", bpm=None, plays=0, likes=0, reposts=0, comments=0,
        downloadable=True, has_downloads_left=True, purchase_url=None, purchase_title=None,
    )


def _cols(path: Path, table: str) -> set[str]:
    with sqlite3.connect(path) as db:
        return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}


def _stored(path: Path, table: str, sc_id: int):
    with sqlite3.connect(path) as db:
        row = db.execute(f"SELECT artist FROM {table} WHERE id = ?", (sc_id,)).fetchone()
    return row[0] if row else "FEHLT"


def _digest_item(seen: Path, tmp_path: Path, sc_id: int):
    res = query_digest(seen, tmp_path / "tracks.sqlite")
    return next(i for i in res.items if i.sc_id == sc_id)


def test_mark_one_and_mark_store_the_artist(tmp_path):
    db = tmp_path / "seen.sqlite"
    with State(db) as st:
        st.mark_one(_track(1, "EXITUS (Halloween Schranz Intro) [FREE DL]", "Jezzn"))
        st.mark([_track(2, "Acid", "Kuko"), _track(3, "Ohne Name", "")])
    assert _stored(db, "seen", 1) == "Jezzn"
    assert _stored(db, "seen", 2) == "Kuko"
    assert _stored(db, "seen", 3) is None


def test_mark_rejected_stores_the_artist(tmp_path):
    db = tmp_path / "seen.sqlite"
    with State(db) as st:
        st.mark_rejected(_track(7, "Fake Upload", "Dubious"), reason="Fake")
    assert _stored(db, "rejected", 7) == "Dubious"


def test_old_tables_get_the_column_and_keep_their_rows(tmp_path):
    db = tmp_path / "seen.sqlite"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE rejected (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reason TEXT, cutoff_hz REAL, rejected_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        c.execute("INSERT INTO seen (id, url, title) VALUES (10, 'u', 'Alt - Titel')")
        c.execute("INSERT INTO rejected (id, url, title, reason) VALUES (11, 'u', 'Alt', 'Fake')")
    with State(db) as st:
        st.mark_one(_track(12, "Neu", "Neuer Artist"))
    assert "artist" in _cols(db, "seen")
    assert "artist" in _cols(db, "rejected")
    assert _stored(db, "seen", 10) is None
    assert _stored(db, "rejected", 11) is None
    assert _stored(db, "seen", 12) == "Neuer Artist"
    with State(db):  # zweites Öffnen darf nicht an der vorhandenen Spalte scheitern
        pass


def test_existing_row_is_not_overwritten_by_insert_or_ignore(tmp_path):
    db = tmp_path / "seen.sqlite"
    with State(db) as st:
        st.mark_one(_track(1, "Titel", "Erster"))
        st.mark_one(_track(1, "Titel", "Zweiter"))
    assert _stored(db, "seen", 1) == "Erster"


def test_digest_uses_stored_artist_when_title_has_no_separator(tmp_path):
    seen = tmp_path / "seen.sqlite"
    with State(seen) as st:
        st.mark_one(_track(1, "EXITUS (Halloween Schranz Intro) [FREE DL]", "  Jezzn "))
    item = _digest_item(seen, tmp_path, 1)
    assert item.artist == "Jezzn"
    assert item.title == "EXITUS (Halloween Schranz Intro) [FREE DL]"


def test_digest_keeps_title_split_when_title_has_separator(tmp_path):
    seen = tmp_path / "seen.sqlite"
    with State(seen) as st:
        st.mark_one(_track(1, "Soy Candela - Toni Massama (HXRT Schranz Edit)", "Uploader"))
    item = _digest_item(seen, tmp_path, 1)
    assert item.artist == "Soy Candela"
    assert item.title == "Toni Massama (HXRT Schranz Edit)"


def test_digest_without_stored_artist_stays_none(tmp_path):
    seen = tmp_path / "seen.sqlite"
    with State(seen) as st:
        st.mark_one(_track(1, "Nur Titel", ""))
    assert _digest_item(seen, tmp_path, 1).artist is None


def test_digest_on_old_database_without_the_column_is_unchanged(tmp_path):
    seen = tmp_path / "seen.sqlite"
    with sqlite3.connect(seen) as c:
        c.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        c.execute("INSERT INTO seen VALUES (1, 'u', 'Nur Titel', '2026-10-27 06:00:00')")
        c.execute("INSERT INTO seen VALUES (2, 'u', 'A - T', '2026-10-27 06:01:00')")
    assert _digest_item(seen, tmp_path, 1).artist is None
    assert _digest_item(seen, tmp_path, 2).artist == "A"


def test_rejected_view_uses_stored_artist_with_the_same_rule(tmp_path):
    db = tmp_path / "seen.sqlite"
    with State(db) as st:
        st.mark_rejected(_track(5, "Ohne Trenner", "Dubious"), reason="Fake")
        st.mark_rejected(_track(6, "Echt - Mit Trenner", "Uploader"), reason="Fake")
    items = {i["id"]: i for i in query_rejected_tracks(db)["items"]}
    assert items[5]["artist"] == "Dubious"
    assert items[6]["artist"] == "Echt"
