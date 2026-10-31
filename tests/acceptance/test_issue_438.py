"""Akzeptanztest: führende Gratis-Hinweise gelten nicht als Teil des Künstlernamens."""
import sqlite3
from pathlib import Path

import pytest

from sc_digger.web.database import query_digest, query_rejected_tracks


def _seen(tmp_path: Path, title: str) -> Path:
    path = tmp_path / "seen.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        db.execute("INSERT INTO seen VALUES (1, 'u', ?, '2026-10-27 06:00:00')", (title,))
    return path


def _digest(tmp_path: Path, title: str):
    res = query_digest(_seen(tmp_path, title), tmp_path / "tracks.sqlite")
    return res.items[0]


@pytest.mark.parametrize("title, artist, rest", [
    ("(FREE DL) Crystal Castles - Vanished ( Bochka Edit)", "Crystal Castles", "Vanished ( Bochka Edit)"),
    ("[Free DL] Scooter - Weekend! (Maeximum Schranz Edit)", "Scooter", "Weekend! (Maeximum Schranz Edit)"),
    ("[FREE DL] LIL PEEP X WICCA PHASE - ABSOLUTE IN DOUBT (NYCTONIAN REMIX)", "LIL PEEP X WICCA PHASE",
     "ABSOLUTE IN DOUBT (NYCTONIAN REMIX)"),
    ("[Free Download] Foo - Bar", "Foo", "Bar"),
    ("[FREEDL]  (free dl) Foo – Bar", "Foo", "Bar"),
])
def test_leading_free_tag_is_removed_from_the_artist(tmp_path, title, artist, rest):
    item = _digest(tmp_path, title)
    assert item.artist == artist
    assert item.title == rest


def test_artist_that_is_only_a_tag_becomes_none(tmp_path):
    item = _digest(tmp_path, "[FREE DL] - Titel")
    assert item.artist is None
    assert item.title == "Titel"


@pytest.mark.parametrize("title, artist", [
    ("Kuko - Tageslicht (PONSK SCHRANZ EDIT)", "Kuko"),
    ("Artist (FREE DL) - Titel", "Artist (FREE DL)"),
    ("Free Spirit - Track", "Free Spirit"),
    ("EXITUS (Halloween Schranz Intro) [FREE DL]", None),
])
def test_other_titles_are_unchanged(tmp_path, title, artist):
    assert _digest(tmp_path, title).artist == artist


def test_rejected_view_removes_the_tag_too(tmp_path):
    path = tmp_path / "seen.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE rejected (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reason TEXT, "
                   "cutoff_hz REAL, rejected_at TEXT)")
        db.execute("INSERT INTO rejected VALUES (1, 'u', '(FREE DL) Crystal Castles - Vanished', 'Fake', 1.0, "
                   "'2026-10-27 06:00:00')")
    assert query_rejected_tracks(path)["items"][0]["artist"] == "Crystal Castles"
