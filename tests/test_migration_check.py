"""Tests für die Prüfung der Migrationsliste vor dem Lauf (Issue #364)."""
import sqlite3

import pytest

from sc_digger import db as db_mod
from sc_digger.db import MIGRATIONS, TrackDB, check_migrations

OK = [(1, "0001_a", "SELECT 1;"), (2, "0002_b", "SELECT 1;")]


def test_echte_liste_ist_sauber():
    assert check_migrations() == []
    assert check_migrations(MIGRATIONS) == []


def test_luecke_und_umgekehrte_reihenfolge_sind_gueltig():
    # Der Runner holt fehlende Versionen nach, die Reihenfolge ist ihm egal.
    assert check_migrations([(5, "0005_e", "SELECT 1;"), (2, "0002_b", "SELECT 1;")]) == []


def test_meldungen_sind_deutsch_und_nennen_die_beteiligten_migrationen():
    probleme = check_migrations([(2, "0002_b", "x"), (2, "0002_c", "y")])
    assert probleme == [
        "Version 2 kommt doppelt vor: '0002_b' und '0002_c'.",
    ]

    probleme = check_migrations([(1, "0001_a", "x"), (2, "0001_a", "y")])
    assert "Name '0001_a' kommt doppelt vor: Version 1 und 2." in probleme


def test_mehrere_probleme_werden_alle_gemeldet():
    probleme = check_migrations(
        [
            (0, "0000_a", "x"),          # Version zu klein
            (2, "0003_b", "x"),          # Name passt nicht zur Version
            (2, "0002_c", "x"),          # doppelte Version
            (3, "", "x"),                # leerer Name
            (4, "0004_d", "x"),
            (5, "0004_d", "x"),          # doppelter Name
        ]
    )
    assert len(probleme) == 6
    text = "\n".join(probleme)
    assert "0000_a" in text
    assert "0003_b" in text
    assert "0002_c" in text
    assert "0004_d" in text


@pytest.mark.parametrize(
    "muell",
    [
        [[]],
        [(1, "0001_a")],
        [None],
        [("1", "0001_a", "x")],
        [(1, None, "x")],
        [1, 2, 3],
        "keine Liste",
    ],
)
def test_check_migrations_wirft_bei_muell_nicht(muell):
    probleme = check_migrations(muell)
    assert isinstance(probleme, list)
    assert all(isinstance(p, str) and p for p in probleme)
    assert probleme


def test_falsche_liste_bricht_vor_jeder_aenderung_ab(tmp_path, monkeypatch):
    monkeypatch.setattr(
        db_mod, "MIGRATIONS", OK + [(2, "0002_c", "CREATE TABLE zweite (id INT);")]
    )
    pfad = tmp_path / "tracks.sqlite"
    with pytest.raises(ValueError, match="Migrationsliste ungültig"):
        TrackDB(pfad)
    with sqlite3.connect(pfad) as conn:
        assert conn.execute("SELECT name FROM sqlite_master").fetchall() == []
