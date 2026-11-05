"""Akzeptanztests: Tägliche Sicherung der SQLite-Datenbanken."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from sc_digger.backup import (BackupError, backup_all, backup_sqlite, prune_backups,
                              restore_sqlite, run_backup)
from sc_digger.models import Config


def _db(path, rows=(1, 2, 3), wal=False):
    con = sqlite3.connect(path)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (x INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [(r,) for r in rows])
    con.commit()
    return con


def _rows(path):
    con = sqlite3.connect(path)
    try:
        return [r[0] for r in con.execute("SELECT x FROM t ORDER BY x")]
    finally:
        con.close()


def _cfg(tmp_path, target):
    return Config({
        "state": {"db_path": str(tmp_path / "data" / "seen.sqlite"),
                  "track_db_path": str(tmp_path / "data" / "tracks.sqlite")},
        "backup": {"target_dir": str(target), "keep_daily": 7, "keep_weekly": 4},
    })


# --- backup_sqlite ---------------------------------------------------------------

def test_backup_contains_uncheckpointed_wal_data_and_is_self_contained(tmp_path):
    src = tmp_path / "tracks.sqlite"
    con = _db(src, wal=True)  # Verbindung bleibt offen: Daten stehen noch im -wal
    try:
        dest = backup_sqlite(src, tmp_path / "out" / "tracks.sqlite")
    finally:
        con.close()
    assert dest == tmp_path / "out" / "tracks.sqlite"
    assert _rows(dest) == [1, 2, 3]
    assert not (tmp_path / "out" / "tracks.sqlite-wal").exists()
    check = sqlite3.connect(dest)
    assert check.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    check.close()


def test_backup_refuses_to_overwrite(tmp_path):
    src = tmp_path / "a.sqlite"
    _db(src).close()
    (tmp_path / "b.sqlite").write_bytes(b"alt")
    with pytest.raises(FileExistsError):
        backup_sqlite(src, tmp_path / "b.sqlite")
    assert (tmp_path / "b.sqlite").read_bytes() == b"alt"


def test_backup_of_a_broken_file_raises_and_leaves_nothing(tmp_path):
    src = tmp_path / "seen.sqlite"
    src.write_bytes(b"das ist keine datenbank" * 100)
    with pytest.raises(BackupError):
        backup_sqlite(src, tmp_path / "out" / "seen.sqlite")
    assert not (tmp_path / "out" / "seen.sqlite").exists()


# --- backup_all ------------------------------------------------------------------

def test_backup_all_writes_a_dated_folder_once_and_skips_missing_sources(tmp_path):
    a, b, cfg_file = tmp_path / "seen.sqlite", tmp_path / "tracks.sqlite", tmp_path / "config.local.yaml"
    _db(a).close()
    _db(b, rows=(7,)).close()
    cfg_file.write_text("x: 1\n", encoding="utf-8")
    target = tmp_path / "backup"
    out = backup_all([a, b, tmp_path / "fehlt.sqlite", cfg_file], target, stamp="2026-11-05")
    day = target / "2026-11-05"
    assert out == [day / "seen.sqlite", day / "tracks.sqlite", day / "config.local.yaml"]
    assert _rows(day / "tracks.sqlite") == [7]
    assert (day / "config.local.yaml").read_text(encoding="utf-8") == "x: 1\n"
    assert backup_all([a, b], target, stamp="2026-11-05") == []
    assert sorted(p.name for p in target.iterdir()) == ["2026-11-05"]


def test_backup_all_leaves_no_half_folder_on_error(tmp_path):
    good, bad = tmp_path / "seen.sqlite", tmp_path / "tracks.sqlite"
    _db(good).close()
    bad.write_bytes(b"kaputt" * 200)
    target = tmp_path / "backup"
    with pytest.raises(BackupError):
        backup_all([good, bad], target, stamp="2026-11-05")
    assert list(target.iterdir()) == []


# --- prune_backups ---------------------------------------------------------------

def test_prune_keeps_7_daily_and_4_weekly(tmp_path):
    start = date(2026, 10, 1)
    for i in range(30):
        (tmp_path / (start + timedelta(days=i)).isoformat()).mkdir()
    (tmp_path / "notizen").mkdir()
    (tmp_path / "liesmich.txt").write_text("x", encoding="utf-8")
    deleted = prune_backups(tmp_path, keep_daily=7, keep_weekly=4)
    kept = sorted(p.name for p in tmp_path.iterdir() if p.name[0].isdigit())
    assert kept == ["2026-10-04", "2026-10-11", "2026-10-18", "2026-10-23", "2026-10-24", "2026-10-25",
                    "2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"]
    assert len(deleted) == 19
    assert deleted == sorted(deleted)
    assert (tmp_path / "notizen").is_dir() and (tmp_path / "liesmich.txt").is_file()


def test_prune_missing_folder_is_empty(tmp_path):
    assert prune_backups(tmp_path / "gibt-es-nicht") == []


# --- restore_sqlite --------------------------------------------------------------

def test_restore_refuses_existing_target_without_force(tmp_path):
    backup, target = tmp_path / "b.sqlite", tmp_path / "seen.sqlite"
    _db(backup, rows=(9,)).close()
    _db(target, rows=(1,)).close()
    with pytest.raises(FileExistsError):
        restore_sqlite(backup, target)
    assert _rows(target) == [1]


def test_restore_with_force_keeps_the_old_file_and_drops_wal(tmp_path):
    backup, target = tmp_path / "b.sqlite", tmp_path / "seen.sqlite"
    _db(backup, rows=(9,)).close()
    _db(target, rows=(1,)).close()
    (tmp_path / "seen.sqlite-wal").write_bytes(b"alt")
    assert restore_sqlite(backup, target, force=True) == target
    assert _rows(target) == [9]
    assert _rows(tmp_path / "seen.sqlite.vor-restore") == [1]
    assert not (tmp_path / "seen.sqlite-wal").exists()


def test_restore_of_a_broken_backup_changes_nothing(tmp_path):
    backup, target = tmp_path / "b.sqlite", tmp_path / "seen.sqlite"
    backup.write_bytes(b"kaputt" * 200)
    _db(target, rows=(1,)).close()
    with pytest.raises(BackupError):
        restore_sqlite(backup, target, force=True)
    assert _rows(target) == [1]


# --- run_backup ------------------------------------------------------------------

def test_run_backup_without_target_is_off(tmp_path):
    notes = []
    cfg = _cfg(tmp_path, "")
    assert run_backup(cfg, today=date(2026, 11, 5), notify=notes.append) == 2
    assert notes == []


def test_run_backup_saves_both_dbs_and_the_override_file(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    _db(tmp_path / "data" / "seen.sqlite").close()
    _db(tmp_path / "data" / "tracks.sqlite", rows=(4,)).close()
    local = tmp_path / "data" / "config.local.yaml"
    local.write_text("health: {}\n", encoding="utf-8")
    monkeypatch.setenv("SC_DIGGER_CONFIG_LOCAL", str(local))
    target = tmp_path / "backup"
    notes = []
    assert run_backup(_cfg(tmp_path, target), today=date(2026, 11, 5), notify=notes.append) == 0
    day = target / "2026-11-05"
    assert sorted(p.name for p in day.iterdir()) == ["config.local.yaml", "seen.sqlite", "tracks.sqlite"]
    assert _rows(day / "tracks.sqlite") == [4]
    assert notes == []


def test_run_backup_failure_notifies_and_returns_1(tmp_path, monkeypatch):
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "seen.sqlite").write_bytes(b"kaputt" * 200)
    notes = []
    assert run_backup(_cfg(tmp_path, tmp_path / "backup"), today=date(2026, 11, 5), notify=notes.append) == 1
    assert len(notes) == 1
    assert "Sicherung fehlgeschlagen" in notes[0]
