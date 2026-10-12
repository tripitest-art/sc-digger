"""Akzeptanztests: Dubletten in der Inbox finden, auflisten und optional verschieben."""
from datetime import datetime
from pathlib import Path

import pytest

from sc_digger import bot as b
from sc_digger import cleanup, main
from sc_digger.cleanup import (
    DuplicateGroup,
    audit_inbox_duplicates,
    find_inbox_duplicates,
    format_duplicate_report,
    move_duplicates,
)
from sc_digger.models import Config
from sc_digger.rekordbox import InboxTrack


def mk(rel: str, key: str = "", bpm: float | None = None, title: str = "T", size: int = 1000) -> InboxTrack:
    return InboxTrack(
        path=Path("/music/inbox") / rel,
        title=title,
        artist="Artist",
        genre="Schranz",
        bpm=bpm,
        key=key,
        comment="",
        duration_s=120.0,
        size=size,
        added=datetime(2026, 10, 1, 12, 0),
    )


def test_find_inbox_duplicates_bevorzugt_getaggt_und_original():
    # ID 10: Ungetaggtes Original vs. getaggte Kopie -> getaggte Kopie gewinnt
    # ID 20: Beide getaggt, Original vs. Kopie -> Original gewinnt
    # ID 30: Einzelner Track -> keine Dublette
    # Ohne ID: wird ignoriert
    tracks = [
        mk("150-155/6A/[10] A - X.wav"),
        mk("150-155/6A/[10] A - X_1.wav", key="6A", bpm=152.0),
        mk("a/[20] B - Y_1.wav", key="7A", bpm=155.0),
        mk("a/[20] B - Y.wav", key="7A", bpm=155.0),
        mk("b/[30] C - Z.wav", key="8A", bpm=160.0),
        mk("b/Ohne_ID.wav", key="8A", bpm=160.0),
    ]
    groups = find_inbox_duplicates(tracks)
    assert len(groups) == 2

    g1, g2 = groups
    assert g1.soundcloud_id == "10"
    assert g1.keep.path.name == "[10] A - X_1.wav"
    assert [d.path.name for d in g1.duplicates] == ["[10] A - X.wav"]

    assert g2.soundcloud_id == "20"
    assert g2.keep.path.name == "[20] B - Y.wav"
    assert [d.path.name for d in g2.duplicates] == ["[20] B - Y_1.wav"]


def test_find_inbox_duplicates_leere_eingabe():
    assert find_inbox_duplicates([]) == []


def test_format_duplicate_report_enthaelt_alle_angaben():
    tracks = [
        mk("155-160/5A/[42] Track.wav", key="5A", bpm=158.0, title="Track", size=10 * 1024 * 1024),
        mk("[42] Track_1.wav", size=10 * 1024 * 1024),
    ]
    groups = find_inbox_duplicates(tracks)
    report = format_duplicate_report(groups, inbox=Path("/music/inbox"))

    assert "42" in report
    assert "[BEHALTEN]" in report
    assert "[ÜBERZÄHLIG]" in report
    assert "155-160/5A/[42] Track.wav" in report
    assert "[42] Track_1.wav" in report
    assert "158.0 BPM" in report
    assert "5A" in report
    assert "10.0 MB" in report


def test_format_duplicate_report_ohne_dubletten():
    assert format_duplicate_report([]) == "Keine Dubletten in der Inbox gefunden."


def test_move_duplicates_verschiebt_nach_rejected_duplicate(tmp_path):
    inbox = tmp_path / "inbox"
    sub_dir = inbox / "155-160" / "5A"
    sub_dir.mkdir(parents=True)

    keep_file = sub_dir / "[100] Hit.wav"
    keep_file.write_bytes(b"keep content")
    dupe_file = inbox / "[100] Hit_1.wav"
    dupe_file.write_bytes(b"dupe content")

    t_keep = InboxTrack(keep_file, "Hit", "A", "", 158.0, "5A", "", 100.0, 12, datetime.now())
    t_dupe = InboxTrack(dupe_file, "Hit", "A", "", None, "", "", 100.0, 12, datetime.now())
    group = DuplicateGroup(soundcloud_id="100", keep=t_keep, duplicates=[t_dupe])

    moved = move_duplicates([group], inbox)

    assert len(moved) == 1
    src, dest = moved[0]
    assert src == dupe_file
    assert dest == inbox / "_rejected" / "duplicate" / "[100] Hit_1.wav"
    assert dest.is_file()
    assert dest.read_bytes() == b"dupe content"
    assert not dupe_file.exists()
    assert keep_file.is_file()
    assert keep_file.read_bytes() == b"keep content"


def test_move_duplicates_kollision_durchnummeriert_statt_ueberschreiben(tmp_path):
    inbox = tmp_path / "inbox"
    rej_dir = inbox / "_rejected" / "duplicate"
    rej_dir.mkdir(parents=True)

    already_there = rej_dir / "[200] Song_1.wav"
    already_there.write_bytes(b"alt")

    to_move = inbox / "[200] Song_1.wav"
    to_move.write_bytes(b"neu")

    t_keep = InboxTrack(inbox / "[200] Song.wav", "Song", "A", "", 150.0, "1A", "", 100.0, 3, datetime.now())
    t_dupe = InboxTrack(to_move, "Song", "A", "", None, "", "", 100.0, 3, datetime.now())
    group = DuplicateGroup("200", t_keep, [t_dupe])

    moved = move_duplicates([group], inbox)

    assert len(moved) == 1
    _, dest = moved[0]
    assert dest == rej_dir / "[200] Song_1_1.wav"
    assert dest.read_bytes() == b"neu"
    assert already_there.read_bytes() == b"alt"


def test_audit_inbox_duplicates_scannt_verzeichnis(tmp_path):
    inbox = tmp_path / "inbox"
    (inbox / "150-155" / "6A").mkdir(parents=True)

    f1 = inbox / "150-155" / "6A" / "[300] Peak.wav"
    f2 = inbox / "[300] Peak_1.wav"
    f1.write_bytes(b"riff dummy")
    f2.write_bytes(b"riff dummy 2")

    groups = audit_inbox_duplicates(inbox)
    assert len(groups) == 1
    assert groups[0].soundcloud_id == "300"
    assert groups[0].keep.path == f1
    assert [d.path for d in groups[0].duplicates] == [f2]


def test_run_cleanup_inbox_dry_run_und_move(tmp_path, capsys):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    f1 = inbox / "[400] Track.wav"
    f2 = inbox / "[400] Track_1.wav"
    f1.write_bytes(b"1")
    f2.write_bytes(b"2")

    cfg = Config({"download": {"inbox_dir": str(inbox)}})

    # 1. Nur auflisten (move=False)
    n = main.run_cleanup_inbox(cfg, move=False, dry_run=False)
    assert n == 1
    assert f1.exists() and f2.exists()
    out = capsys.readouterr().out
    assert "[BEHALTEN]" in out
    assert "[ÜBERZÄHLIG]" in out

    # 2. dry_run mit move=True -> verschiebt nichts
    n_dry = main.run_cleanup_inbox(cfg, move=True, dry_run=True)
    assert n_dry == 1
    assert f1.exists() and f2.exists()

    # 3. move=True -> verschiebt
    n_move = main.run_cleanup_inbox(cfg, move=True, dry_run=False)
    assert n_move == 1
    assert f1.exists()
    assert not f2.exists()
    assert (inbox / "_rejected" / "duplicate" / "[400] Track_1.wav").exists()


def test_bot_cleanup_inbox_reply(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "[500] Bot.wav").write_bytes(b"1")
    (inbox / "[500] Bot_1.wav").write_bytes(b"2")

    cfg = Config({"download": {"inbox_dir": str(inbox)}})
    reply = b.cleanup_inbox_reply(cfg)
    assert "500" in reply
    assert "[BEHALTEN]" in reply
    assert "[ÜBERZÄHLIG]" in reply


def test_bot_hilfetext_nennt_cleanup_inbox():
    assert "/cleanup_inbox" in b.HELP_TEXT
