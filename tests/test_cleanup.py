"""Eigene Tests für sc_digger/cleanup.py: Namenskollisionen, Unicode, Edge-Cases."""
from datetime import datetime
from pathlib import Path

from sc_digger.cleanup import (
    DuplicateGroup,
    audit_inbox_duplicates,
    find_inbox_duplicates,
    format_duplicate_report,
    move_duplicates,
)
from sc_digger.rekordbox import InboxTrack


def _track(path: Path, *, key: str = "", bpm: float | None = None, title: str = "T") -> InboxTrack:
    return InboxTrack(
        path=path,
        title=title,
        artist="Artist",
        genre="Schranz",
        bpm=bpm,
        key=key,
        comment="",
        duration_s=120.0,
        size=1000,
        added=datetime(2026, 10, 1, 12, 0),
    )


def test_move_duplicates_mehrfache_durchnummerierung(tmp_path):
    inbox = tmp_path / "inbox"
    rej_dir = inbox / "_rejected" / "duplicate"
    rej_dir.mkdir(parents=True)

    (rej_dir / "[700] Song_1.wav").write_bytes(b"belegt1")
    (rej_dir / "[700] Song_1_1.wav").write_bytes(b"belegt2")

    to_move = inbox / "[700] Song_1.wav"
    to_move.write_bytes(b"neu")

    keep = _track(inbox / "[700] Song.wav", key="1A", bpm=150.0)
    dupe = _track(to_move)
    group = DuplicateGroup("700", keep, [dupe])

    moved = move_duplicates([group], inbox)

    assert len(moved) == 1
    _, dest = moved[0]
    assert dest == rej_dir / "[700] Song_1_2.wav"
    assert dest.read_bytes() == b"neu"
    assert (rej_dir / "[700] Song_1.wav").read_bytes() == b"belegt1"
    assert (rej_dir / "[700] Song_1_1.wav").read_bytes() == b"belegt2"


def test_move_duplicates_unicode_trackname(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    dupe = inbox / "[800] Üüß Ärger_1.wav"
    dupe.write_bytes(b"x")
    keep = _track(inbox / "[800] Üüß Ärger.wav", key="2A", bpm=148.0)
    group = DuplicateGroup("800", keep, [_track(dupe)])

    moved = move_duplicates([group], inbox)

    assert len(moved) == 1
    _, dest = moved[0]
    assert dest == inbox / "_rejected" / "duplicate" / "[800] Üüß Ärger_1.wav"
    assert dest.is_file()


def test_audit_inbox_duplicates_ordner_fehlt(tmp_path):
    assert audit_inbox_duplicates(tmp_path / "gibtsnicht") == []


def test_find_inbox_duplicates_liefert_leer_ohne_dubletten():
    tracks = [
        _track(Path("/music/inbox/[1] Solo.wav")),
        _track(Path("/music/inbox/ohne_id.wav")),
    ]
    assert find_inbox_duplicates(tracks) == []


def test_format_duplicate_report_leer():
    assert format_duplicate_report([]) == "Keine Dubletten in der Inbox gefunden."
