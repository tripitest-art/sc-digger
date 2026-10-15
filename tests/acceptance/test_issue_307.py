"""Akzeptanztests: Digest erkennt Sammlung und Inbox auch über die gespeicherte SoundCloud-URL."""
import sqlite3
from pathlib import Path

from sc_digger.web.database import query_digest


def _seen(tmp_path: Path, rows: list[tuple[int, str, str]]) -> Path:
    path = tmp_path / "seen.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        for sc_id, url, title in rows:
            db.execute("INSERT INTO seen VALUES (?, ?, ?, '2026-10-14 12:00:00')", (sc_id, url, title))
    return path


def _tracks(tmp_path: Path, rows: list[tuple[str, str, str | None]], with_url_column: bool = True) -> Path:
    path = tmp_path / "tracks.sqlite"
    with sqlite3.connect(path) as db:
        cols = "id INTEGER PRIMARY KEY, path TEXT, status TEXT" + (", source_url TEXT" if with_url_column else "")
        db.execute(f"CREATE TABLE tracks ({cols})")
        for p, status, url in rows:
            if with_url_column:
                db.execute("INSERT INTO tracks (path, status, source_url) VALUES (?, ?, ?)", (p, status, url))
            else:
                db.execute("INSERT INTO tracks (path, status) VALUES (?, ?)", (p, status))
    return path


def _item(res, sc_id: int):
    return next(i for i in res.items if i.sc_id == sc_id)


def test_archive_track_is_found_by_source_url_despite_different_name(tmp_path):
    seen = _seen(tmp_path, [(1, "https://soundcloud.com/dj/acid-kick", "DJ - Acid Kick (Free DL)")])
    tracks = _tracks(tmp_path, [("/sammlung/Komplett anderer Name.wav", "archive", "https://soundcloud.com/dj/acid-kick")])
    item = _item(query_digest(seen, tracks), 1)
    assert item.in_collection is True
    assert item.in_inbox is False
    assert item.download_kind == "native"


def test_url_comparison_ignores_query_case_and_slash(tmp_path):
    seen = _seen(tmp_path, [(1, "https://soundcloud.com/DJ/Acid-Kick?si=abc", "DJ - Acid Kick")])
    tracks = _tracks(tmp_path, [("/sammlung/x.wav", "archive", "http://www.soundcloud.com/dj/acid-kick/")])
    assert _item(query_digest(seen, tracks), 1).in_collection is True


def test_inbox_track_is_found_by_source_url(tmp_path):
    seen = _seen(tmp_path, [(2, "https://soundcloud.com/dj/raw", "DJ - Raw")])
    tracks = _tracks(tmp_path, [("/eingang/150-155/andere.wav", "inbox", "https://soundcloud.com/dj/raw")])
    item = _item(query_digest(seen, tracks), 2)
    assert item.in_inbox is True
    assert item.in_collection is False


def test_rejected_status_and_empty_url_do_not_match(tmp_path):
    seen = _seen(tmp_path, [(3, "https://soundcloud.com/dj/fake", "DJ - Fake"), (4, "", "Leer - Url")])
    tracks = _tracks(tmp_path, [
        ("/eingang/_rejected/x.wav", "rejected", "https://soundcloud.com/dj/fake"),
        ("/sammlung/y.wav", "archive", ""),
        ("/sammlung/z.wav", "archive", None),
    ])
    res = query_digest(seen, tracks)
    for sc_id in (3, 4):
        item = _item(res, sc_id)
        assert item.in_collection is False
        assert item.in_inbox is False


def test_old_tracks_table_without_source_url_column_still_works(tmp_path):
    seen = _seen(tmp_path, [(5, "https://soundcloud.com/dj/a", "DJ - A")])
    tracks = _tracks(tmp_path, [("/sammlung/DJ - A.wav", "archive", None)], with_url_column=False)
    assert _item(query_digest(seen, tracks), 5).in_collection is True  # weiter per Namen
