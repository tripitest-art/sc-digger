"""Akzeptanztest: Rückfüllen von seen.artist und rejected.artist aus tracks.artist."""
import sqlite3
from pathlib import Path

import pytest

from sc_digger.backfill_artist import BackfillResult, backfill_artists, main


def _state(tmp_path: Path, with_column: bool = False) -> Path:
    path = tmp_path / "seen.sqlite"
    extra = ", artist TEXT" if with_column else ""
    with sqlite3.connect(path) as db:
        db.execute(f"CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT{extra})")
        db.execute(
            f"CREATE TABLE rejected (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reason TEXT, "
            f"cutoff_hz REAL, rejected_at TEXT{extra})"
        )
    return path


def _tracks(tmp_path: Path, rows: list[tuple]) -> Path:
    path = tmp_path / "tracks.sqlite"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT, artist TEXT, status TEXT, "
            "source_url TEXT, sc_id INTEGER)"
        )
        for i, (artist, url, sc_id) in enumerate(rows):
            db.execute(
                "INSERT INTO tracks (path, artist, status, source_url, sc_id) VALUES (?, ?, 'archive', ?, ?)",
                (f"/x/{i}.wav", artist, url, sc_id),
            )
    return path


def _artist(path: Path, table: str, row_id: int):
    with sqlite3.connect(path) as db:
        row = db.execute(f"SELECT artist FROM {table} WHERE id = ?", (row_id,)).fetchone()
    return row[0]


def _add_seen(path: Path, row_id: int, url: str, title: str = "T", artist: str | None = None):
    with sqlite3.connect(path) as db:
        cols = {r[1] for r in db.execute("PRAGMA table_info(seen)")}
        if "artist" in cols:
            db.execute("INSERT INTO seen (id, url, title, reported_at, artist) VALUES (?, ?, ?, 'x', ?)",
                       (row_id, url, title, artist))
        else:
            db.execute("INSERT INTO seen (id, url, title, reported_at) VALUES (?, ?, ?, 'x')", (row_id, url, title))


def test_fills_by_normalized_url_and_adds_the_column(tmp_path):
    state = _state(tmp_path)
    _add_seen(state, 1, "https://soundcloud.com/Jezzn/Exitus?si=abc", "EXITUS (Halloween Schranz Intro)")
    tracks = _tracks(tmp_path, [("Jezzn", "http://www.soundcloud.com/jezzn/exitus/", None)])
    res = backfill_artists(state, tracks, apply=True, backup_path=tmp_path / "bak.sqlite")
    assert res == BackfillResult(seen_updated=1, rejected_updated=0, unmatched=0)
    assert _artist(state, "seen", 1) == "Jezzn"


def test_fills_by_sc_id_when_the_url_differs(tmp_path):
    state = _state(tmp_path, with_column=True)
    _add_seen(state, 42, "https://soundcloud.com/a/b")
    tracks = _tracks(tmp_path, [("Kuko", "https://soundcloud.com/ganz/anders", 42)])
    res = backfill_artists(state, tracks, apply=True, backup_path=tmp_path / "bak.sqlite")
    assert res.seen_updated == 1
    assert _artist(state, "seen", 42) == "Kuko"


def test_rejected_rows_are_filled_too(tmp_path):
    state = _state(tmp_path, with_column=True)
    with sqlite3.connect(state) as db:
        db.execute("INSERT INTO rejected (id, url, title, reason, cutoff_hz, rejected_at, artist) "
                   "VALUES (7, 'https://soundcloud.com/d/fake', 'Fake', 'x', 1.0, 'x', NULL)")
    tracks = _tracks(tmp_path, [("Dubious", "https://soundcloud.com/d/fake", None)])
    res = backfill_artists(state, tracks, apply=True, backup_path=tmp_path / "bak.sqlite")
    assert (res.seen_updated, res.rejected_updated) == (0, 1)
    assert _artist(state, "rejected", 7) == "Dubious"


def test_existing_artist_is_never_overwritten_and_blank_matches_count_as_unmatched(tmp_path):
    state = _state(tmp_path, with_column=True)
    _add_seen(state, 1, "https://soundcloud.com/a/eins", artist="Schon da")
    _add_seen(state, 2, "https://soundcloud.com/a/zwei")
    _add_seen(state, 3, "https://soundcloud.com/a/drei")
    _add_seen(state, 4, "")
    tracks = _tracks(tmp_path, [
        ("Anderer", "https://soundcloud.com/a/eins", None),
        ("   ", "https://soundcloud.com/a/zwei", None),
        ("Treffer", "https://soundcloud.com/a/drei", None),
        ("Leere Url", "", None),
    ])
    res = backfill_artists(state, tracks, apply=True, backup_path=tmp_path / "bak.sqlite")
    assert res == BackfillResult(seen_updated=1, rejected_updated=0, unmatched=2)
    assert _artist(state, "seen", 1) == "Schon da"
    assert _artist(state, "seen", 2) is None
    assert _artist(state, "seen", 3) == "Treffer"
    assert _artist(state, "seen", 4) is None


def test_dry_run_reports_but_changes_nothing(tmp_path):
    state = _state(tmp_path)
    _add_seen(state, 1, "https://soundcloud.com/j/e")
    tracks = _tracks(tmp_path, [("Jezzn", "https://soundcloud.com/j/e", None)])
    before_state, before_tracks = state.read_bytes(), tracks.read_bytes()
    res = backfill_artists(state, tracks)
    assert res == BackfillResult(seen_updated=1, rejected_updated=0, unmatched=0)
    assert state.read_bytes() == before_state
    assert tracks.read_bytes() == before_tracks


def test_apply_needs_a_backup_and_never_replaces_one(tmp_path):
    state = _state(tmp_path)
    _add_seen(state, 1, "https://soundcloud.com/j/e", "Titel")
    tracks = _tracks(tmp_path, [("Jezzn", "https://soundcloud.com/j/e", None)])
    before = state.read_bytes()
    with pytest.raises(ValueError):
        backfill_artists(state, tracks, apply=True)
    existing = tmp_path / "bak.sqlite"
    existing.write_bytes(b"nicht anfassen")
    with pytest.raises(FileExistsError):
        backfill_artists(state, tracks, apply=True, backup_path=existing)
    assert existing.read_bytes() == b"nicht anfassen"
    assert state.read_bytes() == before

    fresh = tmp_path / "neu.sqlite"
    backfill_artists(state, tracks, apply=True, backup_path=fresh)
    with sqlite3.connect(fresh) as db:
        assert db.execute("SELECT id, url, title FROM seen").fetchall() == [(1, "https://soundcloud.com/j/e", "Titel")]
        assert "artist" not in {r[1] for r in db.execute("PRAGMA table_info(seen)")}


def test_missing_files_raise_file_not_found(tmp_path):
    state = _state(tmp_path)
    tracks = _tracks(tmp_path, [])
    with pytest.raises(FileNotFoundError):
        backfill_artists(tmp_path / "gibt-es-nicht.sqlite", tracks)
    with pytest.raises(FileNotFoundError):
        backfill_artists(state, tmp_path / "gibt-es-nicht.sqlite")


def test_cli_dry_run_apply_and_missing_backup(tmp_path, capsys):
    state = _state(tmp_path)
    _add_seen(state, 1, "https://soundcloud.com/j/e")
    tracks = _tracks(tmp_path, [("Jezzn", "https://soundcloud.com/j/e", None)])
    base = ["--state-db", str(state), "--track-db", str(tracks)]

    assert main(base) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "seen_updated=1 rejected_updated=0 unmatched=0"
    assert out[1] == "Probelauf: nichts geschrieben"

    with pytest.raises(SystemExit) as exc:
        main([*base, "--apply"])
    assert exc.value.code == 2
    capsys.readouterr()

    assert main([*base, "--apply", "--backup", str(tmp_path / "bak.sqlite")]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "seen_updated=1 rejected_updated=0 unmatched=0"
    assert _artist(state, "seen", 1) == "Jezzn"