"""Akzeptanztests: Digest erkennt aussortierte Duplikate der Inbox als „in Sammlung“."""
import sqlite3
from pathlib import Path

from sc_digger.web.database import query_digest


def _seen(tmp_path: Path, rows: list[tuple[int, str]]) -> Path:
    path = tmp_path / "seen.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        for sc_id, title in rows:
            db.execute("INSERT INTO seen VALUES (?, 'u', ?, '2026-10-14 12:00:00')", (sc_id, title))
    return path


def _touch(inbox: Path, rel: str) -> None:
    f = inbox / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"")


def _item(res, sc_id: int):
    return next(i for i in res.items if i.sc_id == sc_id)


def test_duplicate_with_sc_id_counts_as_collection(tmp_path):
    inbox = tmp_path / "inbox"
    _touch(inbox, "_rejected/duplicate/[555] Zatox - Dopamin.wav")
    res = query_digest(_seen(tmp_path, [(555, "Zatox - Dopamin")]), tmp_path / "t.sqlite", inbox_dir=inbox)
    item = _item(res, 555)
    assert item.in_collection is True
    assert item.in_inbox is False
    assert item.download_kind == "native"


def test_duplicate_without_sc_id_matches_by_name(tmp_path):
    inbox = tmp_path / "inbox"
    _touch(inbox, "_rejected/duplicate/Boys Noize - FVKVRVND.wav")
    res = query_digest(_seen(tmp_path, [(7, "Boys Noize - FVKVRVND")]), tmp_path / "t.sqlite", inbox_dir=inbox)
    assert _item(res, 7).in_collection is True


def test_duplicate_is_found_by_collection_filter(tmp_path):
    inbox = tmp_path / "inbox"
    _touch(inbox, "_rejected/duplicate/[555] Zatox - Dopamin.wav")
    seen = _seen(tmp_path, [(555, "Zatox - Dopamin"), (556, "Anderer - Track")])
    res = query_digest(seen, tmp_path / "t.sqlite", inbox_dir=inbox, status_filter="collection")
    assert [i.sc_id for i in res.items] == [555]


def test_other_rejected_folders_stay_ignored(tmp_path):
    inbox = tmp_path / "inbox"
    _touch(inbox, "_rejected/clipped/[600] Zatox - Dopamin.wav")
    _touch(inbox, "_rejected/[601] Boys Noize - FVKVRVND.wav")
    seen = _seen(tmp_path, [(600, "Zatox - Dopamin"), (601, "Boys Noize - FVKVRVND")])
    res = query_digest(seen, tmp_path / "t.sqlite", inbox_dir=inbox)
    for sc_id in (600, 601):
        item = _item(res, sc_id)
        assert item.in_collection is False
        assert item.in_inbox is False
        assert item.download_kind == "stream"


def test_regular_inbox_file_is_still_inbox(tmp_path):
    inbox = tmp_path / "inbox"
    _touch(inbox, "150-155/[700] Svetec - Acid Kick.wav")
    res = query_digest(_seen(tmp_path, [(700, "Svetec - Acid Kick")]), tmp_path / "t.sqlite", inbox_dir=inbox)
    item = _item(res, 700)
    assert item.in_inbox is True
    assert item.in_collection is False
