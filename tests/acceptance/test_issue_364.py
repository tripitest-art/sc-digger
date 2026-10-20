"""Akzeptanztests: Migrationsliste wird geprüft, bevor eine Migration läuft."""
import sqlite3

import pytest

from sc_digger import db as db_mod
from sc_digger.db import MIGRATIONS, TrackDB, check_migrations

OK = [(1, "0001_a", "SELECT 1;"), (2, "0002_b", "SELECT 1;")]


def test_the_real_migration_list_is_consistent():
    assert check_migrations(MIGRATIONS) == []
    assert check_migrations() == []


def test_valid_list_with_a_gap_is_allowed():
    assert check_migrations([(1, "0001_a", "SELECT 1;"), (3, "0003_c", "SELECT 1;")]) == []


def test_duplicate_version_is_reported_with_both_names():
    problems = check_migrations(OK + [(2, "0002_c", "SELECT 1;")])
    assert len(problems) == 1
    assert "2" in problems[0] and "0002_b" in problems[0] and "0002_c" in problems[0]


def test_duplicate_name_is_reported():
    problems = check_migrations([(1, "0001_a", "SELECT 1;"), (2, "0001_a", "SELECT 1;")])
    assert any("0001_a" in p for p in problems)


@pytest.mark.parametrize("entry", [
    (3, "0004_x", "SELECT 1;"),     # Name passt nicht zur Version
    (3, "3_x", "SELECT 1;"),        # Version nicht vierstellig aufgefüllt
    (0, "0000_x", "SELECT 1;"),     # Version muss >= 1 sein
    (True, "0001_x", "SELECT 1;"),  # bool ist keine Version
    (4, "", "SELECT 1;"),           # leerer Name
])
def test_invalid_entries_are_reported(entry):
    problems = check_migrations(OK + [entry])
    assert problems and all(isinstance(p, str) and p for p in problems)


def test_opening_the_db_with_a_bad_list_fails_clearly_and_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "MIGRATIONS", OK + [(2, "0002_c", "CREATE TABLE zweite (id INT);")])
    path = tmp_path / "tracks.sqlite"
    with pytest.raises(ValueError) as exc:
        TrackDB(path)
    assert "0002_b" in str(exc.value) and "0002_c" in str(exc.value)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master").fetchall() == []


def test_a_valid_list_still_opens_the_db(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        versions = [r[0] for r in db.db.execute("SELECT version FROM schema_migrations")]
    assert sorted(versions) == sorted(v for v, _, _ in MIGRATIONS)
