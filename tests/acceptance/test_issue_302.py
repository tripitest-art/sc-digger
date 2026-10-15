"""Akzeptanztests: Werbe-Zusätze in Klammern gelten nicht als Remixer."""
import sqlite3
from pathlib import Path

import pytest

from sc_digger.collection import remixer_tokens
from sc_digger.web.database import query_digest


@pytest.mark.parametrize("title", [
    "Pull me Closer (EXCLU)",
    "Unknown Rhythms (FINAL MASTER)",
    "Where It Remains [Premiere]",
    "Some Track (Out Now)",
    "Some Track [Exclusive Preview]",
])
def test_promo_brackets_give_no_remixer(title):
    assert remixer_tokens(title) == set()


@pytest.mark.parametrize("title, expected", [
    ("Numb (Might Be Twins Schranz Remix)", {"might", "be", "twins"}),
    ("Hit (Final Round Edit)", {"final", "round"}),
    ("Hit (Exclusive Remix by Vrtgø) [FREE DL]", {"exclusive", "by", "vrtgø"}),
    ("Hit (Original Mix)", set()),
])
def test_real_remixers_are_kept(title, expected):
    assert remixer_tokens(title) == expected


def test_digest_matches_inbox_file_with_promo_suffix(tmp_path: Path):
    seen = tmp_path / "seen.sqlite"
    with sqlite3.connect(seen) as db:
        db.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        db.execute("INSERT INTO seen VALUES (1, 'u', 'COIL ft. Ray - Pull me Closer', '2026-10-14 12:00:00')")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "COIL ft. Ray - Pull me Closer (EXCLU).wav").write_bytes(b"")
    res = query_digest(seen, tmp_path / "t.sqlite", inbox_dir=inbox)
    assert res.items[0].in_inbox is True


def test_different_remix_still_does_not_match(tmp_path: Path):
    seen = tmp_path / "seen.sqlite"
    with sqlite3.connect(seen) as db:
        db.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        db.execute("INSERT INTO seen VALUES (1, 'u', 'Linkin Park - Numb (Schranz Edit)', '2026-10-14 12:00:00')")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "Linkin Park - Numb (Might Be Twins Schranz Remix).wav").write_bytes(b"")
    res = query_digest(seen, tmp_path / "t.sqlite", inbox_dir=inbox)
    assert res.items[0].in_inbox is False
