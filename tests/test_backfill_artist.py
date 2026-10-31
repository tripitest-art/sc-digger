"""Unit-Tests für sc_digger.backfill_artist (Randfälle)."""
import sqlite3
from pathlib import Path
import pytest

from sc_digger.backfill_artist import BackfillResult, backfill_artists


def _create_state_db(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT, artist TEXT)")
        db.execute(
            "CREATE TABLE rejected (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reason TEXT, "
            "cutoff_hz REAL, rejected_at TEXT, artist TEXT)"
        )


def _create_tracks_db(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT, artist TEXT, status TEXT, "
            "source_url TEXT, sc_id INTEGER)"
        )


def test_multiple_tracks_rows_for_same_sc_id_picks_non_empty_artist(tmp_path: Path):
    state_path = tmp_path / "state.sqlite"
    _create_state_db(state_path)
    with sqlite3.connect(state_path) as db:
        db.execute("INSERT INTO seen (id, url, title, reported_at, artist) VALUES (?, ?, ?, ?, ?)", (10, 'http://x', 'T', 'r', None))

    track_path = tmp_path / "tracks.sqlite"
    _create_tracks_db(track_path)
    with sqlite3.connect(track_path) as db:
        # Erste Zeile leerer Artist, zweite Zeile echter Artist
        db.execute("INSERT INTO tracks (id, path, artist, status, source_url, sc_id) VALUES (?, ?, ?, ?, ?, ?)", (1, 'p1', '   ', 'archive', 'http://a', 10))
        db.execute("INSERT INTO tracks (id, path, artist, status, source_url, sc_id) VALUES (?, ?, ?, ?, ?, ?)", (2, 'p2', 'Valid Artist', 'archive', 'http://b', 10))

    res = backfill_artists(state_path, track_path, apply=True, backup_path=tmp_path / "bak.sqlite")
    assert res == BackfillResult(seen_updated=1, rejected_updated=0, unmatched=0)

    with sqlite3.connect(state_path) as db:
        row = db.execute("SELECT artist FROM seen WHERE id = 10").fetchone()
        assert row[0] == "Valid Artist"


def test_multiple_tracks_rows_for_same_url_picks_non_empty_artist(tmp_path: Path):
    state_path = tmp_path / "state.sqlite"
    _create_state_db(state_path)
    with sqlite3.connect(state_path) as db:
        db.execute("INSERT INTO seen (id, url, title, reported_at, artist) VALUES (?, ?, ?, ?, ?)", (100, 'https://soundcloud.com/dj/track', 'T', 'r', None))

    track_path = tmp_path / "tracks.sqlite"
    _create_tracks_db(track_path)
    with sqlite3.connect(track_path) as db:
        db.execute("INSERT INTO tracks (id, path, artist, status, source_url, sc_id) VALUES (?, ?, ?, ?, ?, ?)", (1, 'p1', None, 'archive', 'https://soundcloud.com/dj/track/', None))
        db.execute("INSERT INTO tracks (id, path, artist, status, source_url, sc_id) VALUES (?, ?, ?, ?, ?, ?)", (2, 'p2', 'URL Artist', 'archive', 'https://soundcloud.com/dj/track?ref=clipboard', None))

    res = backfill_artists(state_path, track_path, apply=True, backup_path=tmp_path / "bak.sqlite")
    assert res == BackfillResult(seen_updated=1, rejected_updated=0, unmatched=0)

    with sqlite3.connect(state_path) as db:
        row = db.execute("SELECT artist FROM seen WHERE id = 100").fetchone()
        assert row[0] == "URL Artist"


def test_special_characters_and_whitespace_in_urls(tmp_path: Path):
    state_path = tmp_path / "state.sqlite"
    _create_state_db(state_path)
    with sqlite3.connect(state_path) as db:
        db.execute("INSERT INTO seen (id, url, title, reported_at, artist) VALUES (?, ?, ?, ?, ?)", (1, '  https://soundcloud.com/artist-name/track-t%C3%BCtel?si=123#t=0  ', 'T', 'r', None))

    track_path = tmp_path / "tracks.sqlite"
    _create_tracks_db(track_path)
    with sqlite3.connect(track_path) as db:
        db.execute("INSERT INTO tracks (id, path, artist, status, source_url, sc_id) VALUES (?, ?, ?, ?, ?, ?)", (1, 'p', 'Special Artist', 'archive', 'http://soundcloud.com/artist-name/track-t%C3%BCtel/', None))

    res = backfill_artists(state_path, track_path, apply=True, backup_path=tmp_path / "bak.sqlite")
    assert res == BackfillResult(seen_updated=1, rejected_updated=0, unmatched=0)

    with sqlite3.connect(state_path) as db:
        row = db.execute("SELECT artist FROM seen WHERE id = 1").fetchone()
        assert row[0] == "Special Artist"


def test_whitespace_only_existing_artist_is_overwritten(tmp_path: Path):
    state_path = tmp_path / "state.sqlite"
    _create_state_db(state_path)
    with sqlite3.connect(state_path) as db:
        db.execute("INSERT INTO seen (id, url, title, reported_at, artist) VALUES (?, ?, ?, ?, ?)", (1, 'https://soundcloud.com/a/b', 'T', 'r', '   '))

    track_path = tmp_path / "tracks.sqlite"
    _create_tracks_db(track_path)
    with sqlite3.connect(track_path) as db:
        db.execute("INSERT INTO tracks (id, path, artist, status, source_url, sc_id) VALUES (?, ?, ?, ?, ?, ?)", (1, 'p', 'New Artist', 'archive', 'https://soundcloud.com/a/b', None))

    res = backfill_artists(state_path, track_path, apply=True, backup_path=tmp_path / "bak.sqlite")
    assert res == BackfillResult(seen_updated=1, rejected_updated=0, unmatched=0)

    with sqlite3.connect(state_path) as db:
        row = db.execute("SELECT artist FROM seen WHERE id = 1").fetchone()
        assert row[0] == "New Artist"


def test_missing_tables_in_state_and_tracks(tmp_path: Path):
    state_path = tmp_path / "empty_state.sqlite"
    with sqlite3.connect(state_path) as db:
        db.execute("CREATE TABLE other (x INT)")

    track_path = tmp_path / "empty_tracks.sqlite"
    with sqlite3.connect(track_path) as db:
        db.execute("CREATE TABLE other (x INT)")

    res = backfill_artists(state_path, track_path)
    assert res == BackfillResult(seen_updated=0, rejected_updated=0, unmatched=0)


def test_missing_optional_columns_in_tracks(tmp_path: Path):
    state_path = tmp_path / "state.sqlite"
    _create_state_db(state_path)
    with sqlite3.connect(state_path) as db:
        db.execute("INSERT INTO seen (id, url, title, reported_at, artist) VALUES (?, ?, ?, ?, ?)", (1, 'https://soundcloud.com/a/b', 'T', 'r', None))

    track_path = tmp_path / "tracks_no_cols.sqlite"
    with sqlite3.connect(track_path) as db:
        # tracks-Tabelle hat weder sc_id noch source_url noch artist
        db.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT)")
        db.execute("INSERT INTO tracks (id, path) VALUES (1, 'p')")

    res = backfill_artists(state_path, track_path)
    assert res == BackfillResult(seen_updated=0, rejected_updated=0, unmatched=1)


def test_large_tables_performance_and_accuracy(tmp_path: Path):
    state_path = tmp_path / "large_state.sqlite"
    _create_state_db(state_path)
    track_path = tmp_path / "large_tracks.sqlite"
    _create_tracks_db(track_path)

    n_rows = 1000
    seen_rows = []
    rejected_rows = []
    track_rows = []

    for i in range(1, n_rows + 1):
        url = f"https://soundcloud.com/artist{i}/track{i}"
        artist = f"Artist {i}"
        if i % 2 == 0:
            seen_rows.append((i, url, f"Title {i}", "2026-10-31", None))
        else:
            rejected_rows.append((i, url, f"Title {i}", "fake", 15.0, "2026-10-31", None))
        track_rows.append((i, f"/path/{i}.wav", artist, "archive", url, i))

    with sqlite3.connect(state_path) as db:
        db.executemany("INSERT INTO seen VALUES (?, ?, ?, ?, ?)", seen_rows)
        db.executemany("INSERT INTO rejected VALUES (?, ?, ?, ?, ?, ?, ?)", rejected_rows)

    with sqlite3.connect(track_path) as db:
        db.executemany("INSERT INTO tracks VALUES (?, ?, ?, ?, ?, ?)", track_rows)

    res = backfill_artists(state_path, track_path, apply=True, backup_path=tmp_path / "bak.sqlite")
    assert res == BackfillResult(seen_updated=500, rejected_updated=500, unmatched=0)

    with sqlite3.connect(state_path) as db:
        sample_seen = db.execute("SELECT artist FROM seen WHERE id = 100").fetchone()
        assert sample_seen[0] == "Artist 100"
        sample_rej = db.execute("SELECT artist FROM rejected WHERE id = 101").fetchone()
        assert sample_rej[0] == "Artist 101"
