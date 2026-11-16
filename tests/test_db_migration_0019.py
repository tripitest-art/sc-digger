"""Upgrade-Pfad der Profil-Migration (Issue #561, ``0020_profiles``).

Der Dateiname trägt noch die 19 aus der ersten Fassung des Issues; die Migration selbst ist
die 20, weil 19 schon durch ``0019_tracks_helligkeit`` belegt war. Die Tests suchen sie
deshalb über den Namen, nicht über die Nummer.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sc_digger import db as db_mod
from sc_digger.db import DEFAULT_PROFILE, MIGRATIONS, TrackDB
from sc_digger.taste import TasteModel, load_taste_model, save_taste_model

PROFIL_VERSION = next(v for v, name, _ in MIGRATIONS if name.endswith("_profiles"))


def _alte_db(pfad: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Baut eine Datenbank auf dem Stand vor der Profil-Migration und füllt sie.

    Schreibt bewusst mit rohem SQL in der alten Tabellenform (``taste_model.id``) und
    committet vor dem Schließen – ``TrackDB.close`` tut das nicht.
    """
    monkeypatch.setattr(db_mod, "MIGRATIONS", [m for m in MIGRATIONS if m[0] < PROFIL_VERSION])
    with TrackDB(pfad) as db:
        db.db.executemany(
            "INSERT INTO tracks (path, mtime, size, status, artist, title) VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("/inbox/a.flac", 1.0, 10, "inbox", "A", "Eins"),
                ("/inbox/b.flac", 2.0, 20, "inbox", "B", "Zwei"),
                ("/music/c.flac", 3.0, 30, "archive", "C", "Drei"),
            ],
        )
        db.db.execute(
            "INSERT INTO taste_model (id, embedding_model, trained_at, n_pos, n_neg, model_json) "
            "VALUES (1, 'maest', '2026-11-01 07:05:00', 7, 5, '{\"weights\": [1.0]}')"
        )
        db.db.executemany(
            "INSERT INTO store_items "
            "(id, sc_id, title, artist, purchase_url, purchase_title, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (3, 4711, "T1", "A1", "https://example.test/1", "Buy", "2026-10-01 08:00:00", "2026-10-02 08:00:00"),
                (7, 4712, "T2", "A2", "https://example.test/2", None, "2026-10-03 08:00:00", "2026-10-04 08:00:00"),
            ],
        )
        db.db.executemany(
            "INSERT INTO gate_items "
            "(id, sc_id, title, artist, gate_url, gate_type, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (2, 811, "G1", "A1", "https://example.test/g1", "hypeddit", "2026-10-05 08:00:00", "2026-10-06 08:00:00"),
                (9, 812, "G2", "A2", "https://example.test/g2", None, "2026-10-07 08:00:00", "2026-10-08 08:00:00"),
            ],
        )
        db.db.executemany(
            "INSERT INTO exploration_tag_stats (tag, uses, successes, last_used_at, last_success_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("tribal", 3, 2, "2026-11-01 07:00:00", "2026-10-30 07:00:00"),
                ("hardgroove", 5, 0, "2026-11-02 07:00:00", None),
            ],
        )
        db.db.commit()
    monkeypatch.undo()


def _zeilen(db: TrackDB, sql: str) -> list[tuple]:
    return [tuple(r) for r in db.db.execute(sql).fetchall()]


def test_alte_db_steht_vor_der_profil_migration(tmp_path, monkeypatch):
    """Sichert die Vorbedingung: sonst prüften die Upgrade-Tests nur einen Neuaufbau."""
    pfad = tmp_path / "alt.sqlite"
    _alte_db(pfad, monkeypatch)
    con = sqlite3.connect(pfad)
    try:
        versionen = {r[0] for r in con.execute("SELECT version FROM schema_migrations")}
        spalten = {r[1] for r in con.execute("PRAGMA table_info(tracks)")}
        assert PROFIL_VERSION not in versionen
        assert max(versionen) == PROFIL_VERSION - 1
        assert "profile_id" not in spalten
        assert con.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 3
    finally:
        con.close()


def test_upgrade_uebernimmt_alle_zeilen_unveraendert(tmp_path, monkeypatch):
    pfad = tmp_path / "alt.sqlite"
    _alte_db(pfad, monkeypatch)

    with TrackDB(pfad) as db:
        assert _zeilen(db, "SELECT version, name FROM schema_migrations WHERE version = "
                           f"{PROFIL_VERSION}") == [(PROFIL_VERSION, f"{PROFIL_VERSION:04d}_profiles")]
        assert _zeilen(db, "SELECT profile_id, path, size, status, title FROM tracks ORDER BY id") == [
            ("schranz", "/inbox/a.flac", 10, "inbox", "Eins"),
            ("schranz", "/inbox/b.flac", 20, "inbox", "Zwei"),
            ("schranz", "/music/c.flac", 30, "archive", "Drei"),
        ]
        assert _zeilen(db, "SELECT profile_id, embedding_model, trained_at, n_pos, n_neg, model_json "
                           "FROM taste_model") == [
            ("schranz", "maest", "2026-11-01 07:05:00", 7, 5, '{"weights": [1.0]}'),
        ]
        assert _zeilen(db, "SELECT id, profile_id, sc_id, title, artist, purchase_url, purchase_title, "
                           "first_seen, last_seen FROM store_items ORDER BY id") == [
            (3, "schranz", 4711, "T1", "A1", "https://example.test/1", "Buy",
             "2026-10-01 08:00:00", "2026-10-02 08:00:00"),
            (7, "schranz", 4712, "T2", "A2", "https://example.test/2", None,
             "2026-10-03 08:00:00", "2026-10-04 08:00:00"),
        ]
        assert _zeilen(db, "SELECT id, profile_id, sc_id, title, artist, gate_url, gate_type, "
                           "first_seen, last_seen FROM gate_items ORDER BY id") == [
            (2, "schranz", 811, "G1", "A1", "https://example.test/g1", "hypeddit",
             "2026-10-05 08:00:00", "2026-10-06 08:00:00"),
            (9, "schranz", 812, "G2", "A2", "https://example.test/g2", None,
             "2026-10-07 08:00:00", "2026-10-08 08:00:00"),
        ]
        assert _zeilen(db, "SELECT profile_id, tag, uses, successes, last_used_at, last_success_at "
                           "FROM exploration_tag_stats ORDER BY tag") == [
            ("schranz", "hardgroove", 5, 0, "2026-11-02 07:00:00", None),
            ("schranz", "tribal", 3, 2, "2026-11-01 07:00:00", "2026-10-30 07:00:00"),
        ]


def test_upgrade_raeumt_hilfstabellen_weg_und_legt_indizes_neu_an(tmp_path, monkeypatch):
    pfad = tmp_path / "alt.sqlite"
    _alte_db(pfad, monkeypatch)
    with TrackDB(pfad) as db:
        namen = {r[0] for r in db.db.execute("SELECT name FROM sqlite_master")}
        assert not {n for n in namen if n.endswith("_neu")}
        # DROP TABLE nimmt die Indizes mit; ohne Neuanlage wäre die Kaufliste langsam sortiert.
        assert {"idx_store_items_last_seen", "idx_gate_items_last_seen"} <= namen
        index_tabellen = dict(db.db.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_%_last_seen'"
        ).fetchall())
        assert index_tabellen["idx_store_items_last_seen"] == "store_items"
        assert index_tabellen["idx_gate_items_last_seen"] == "gate_items"


def test_autoincrement_zaehlt_nach_dem_upgrade_weiter(tmp_path, monkeypatch):
    """Neue Zeilen dürfen keine alte id wiederverwenden (höchste alte id: 7 bzw. 9)."""
    pfad = tmp_path / "alt.sqlite"
    _alte_db(pfad, monkeypatch)
    with TrackDB(pfad) as db:
        db.upsert_store_item(5000, "Neu", "N", "https://example.test/neu")
        db.upsert_gate_item(900, "Neu", "N", "https://example.test/gneu")
        assert db.db.execute("SELECT id FROM store_items WHERE sc_id = 5000").fetchone()[0] == 8
        assert db.db.execute("SELECT id FROM gate_items WHERE sc_id = 900").fetchone()[0] == 10


def test_upserts_aktualisieren_nach_dem_upgrade_statt_zu_verdoppeln(tmp_path, monkeypatch):
    """Die Schreibpfade nennen kein Profil; ihr Konfliktziel muss zum neuen Schlüssel passen."""
    pfad = tmp_path / "alt.sqlite"
    _alte_db(pfad, monkeypatch)
    with TrackDB(pfad) as db:
        db.upsert_store_item(4711, "T1 neu", "A1", "https://example.test/1b")
        db.upsert_gate_item(811, "G1 neu", "A1", "https://example.test/g1b")
        db.record_exploration_use("tribal", True, now=datetime(2026, 11, 16, 7, 0, tzinfo=timezone.utc))

        assert _zeilen(db, "SELECT id, profile_id, title, purchase_url, purchase_title, first_seen "
                           "FROM store_items WHERE sc_id = 4711") == [
            (3, "schranz", "T1 neu", "https://example.test/1b", "Buy", "2026-10-01 08:00:00"),
        ]
        assert _zeilen(db, "SELECT id, profile_id, title, gate_url, gate_type FROM gate_items "
                           "WHERE sc_id = 811") == [
            (2, "schranz", "G1 neu", "https://example.test/g1b", "hypeddit"),
        ]
        assert _zeilen(db, "SELECT profile_id, uses, successes, last_success_at "
                           "FROM exploration_tag_stats WHERE tag = 'tribal'") == [
            ("schranz", 4, 3, "2026-11-16 07:00:00"),
        ]
        assert db.db.execute("SELECT COUNT(*) FROM store_items").fetchone()[0] == 2
        assert db.db.execute("SELECT COUNT(*) FROM gate_items").fetchone()[0] == 2
        assert db.db.execute("SELECT COUNT(*) FROM exploration_tag_stats").fetchone()[0] == 2


def test_bestandsmodell_bleibt_nach_dem_upgrade_ladbar_und_ersetzbar(tmp_path, monkeypatch):
    pfad = tmp_path / "alt.sqlite"
    monkeypatch.setattr(db_mod, "MIGRATIONS", [m for m in MIGRATIONS if m[0] < PROFIL_VERSION])
    alt = TasteModel(weights=[1.0, 0.0], bias=0.5, mean=[0.0, 0.0], std=[1.0, 1.0])
    with TrackDB(pfad) as db:
        db.db.execute(
            "INSERT INTO taste_model (id, embedding_model, trained_at, n_pos, n_neg, model_json) "
            "VALUES (1, 'maest', '2026-11-01 07:05:00', 7, 5, ?)",
            (json.dumps(alt.to_dict()),),
        )
        db.db.commit()
    monkeypatch.undo()

    with TrackDB(pfad) as db:
        assert db.profile_id == DEFAULT_PROFILE
        assert load_taste_model(db) == ("maest", alt)
        neu = TasteModel(weights=[0.0, 2.0], bias=0.0, mean=[0.0, 0.0], std=[1.0, 1.0])
        save_taste_model(db, neu, "maest2", n_pos=9, n_neg=8, trained_at="2026-11-16 07:05:00")
        assert load_taste_model(db) == ("maest2", neu)
        assert _zeilen(db, "SELECT profile_id, n_pos, n_neg, trained_at FROM taste_model") == [
            ("schranz", 9, 8, "2026-11-16 07:05:00"),
        ]


def test_taste_modelle_verschiedener_profile_stoeren_sich_nicht(tmp_path):
    pfad = tmp_path / "t.sqlite"
    schranz = TasteModel(weights=[1.0], bias=0.0, mean=[0.0], std=[1.0])
    house = TasteModel(weights=[-1.0], bias=0.0, mean=[0.0], std=[1.0])
    with TrackDB(pfad) as db:
        save_taste_model(db, schranz, "maest", n_pos=5, n_neg=5, trained_at="2026-11-16 07:05:00")
    with TrackDB(pfad, profile_id="house") as db:
        assert load_taste_model(db) is None, "ein fremdes Profil sieht das Schranz-Modell nicht"
        save_taste_model(db, house, "mfcc", n_pos=6, n_neg=7, trained_at="2026-11-16 08:00:00")
        assert load_taste_model(db) == ("mfcc", house)
    with TrackDB(pfad) as db:
        assert load_taste_model(db) == ("maest", schranz)
        assert _zeilen(db, "SELECT profile_id, embedding_model FROM taste_model ORDER BY profile_id") == [
            ("house", "mfcc"), ("schranz", "maest"),
        ]


def test_unlesbares_modell_json_ergibt_none(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        db.db.execute(
            "INSERT INTO taste_model (profile_id, embedding_model, trained_at, n_pos, n_neg, model_json) "
            "VALUES ('schranz', 'maest', '2026-11-16 07:05:00', 5, 5, '{kaputt')"
        )
        db.db.commit()
        assert load_taste_model(db) is None


def test_taste_model_id_bleibt_fuer_alte_leser_lesbar(tmp_path, monkeypatch):
    """Web-Dashboard und ältere Akzeptanztests lesen noch ``WHERE id = 1``."""
    pfad = tmp_path / "alt.sqlite"
    _alte_db(pfad, monkeypatch)
    with TrackDB(pfad) as db:
        assert _zeilen(db, "SELECT id, profile_id, embedding_model FROM taste_model WHERE id = 1") == [
            (1, "schranz", "maest"),
        ]
        # Die Spalte ist berechnet: Schreiben ist nicht möglich, die alte Einzeiler-Sperre ist weg.
        with pytest.raises(sqlite3.OperationalError):
            db.db.execute("UPDATE taste_model SET id = 2")


def test_gescheiterte_migration_laesst_die_alte_db_unveraendert(tmp_path, monkeypatch):
    """Bricht der Neubau mittendrin ab, darf kein halber Stand zurückbleiben."""
    pfad = tmp_path / "alt.sqlite"
    _alte_db(pfad, monkeypatch)
    kaputt = [
        (v, name, script + "\nSELECT * FROM gibt_es_nicht;" if v == PROFIL_VERSION else script)
        for v, name, script in MIGRATIONS
    ]
    monkeypatch.setattr(db_mod, "MIGRATIONS", kaputt)
    with pytest.raises(sqlite3.OperationalError):
        TrackDB(pfad)
    monkeypatch.undo()

    con = sqlite3.connect(pfad)
    try:
        assert "profile_id" not in {r[1] for r in con.execute("PRAGMA table_info(tracks)")}
        assert "id" in {r[1] for r in con.execute("PRAGMA table_info(taste_model)")}
        assert con.execute("SELECT COUNT(*) FROM store_items").fetchone()[0] == 2
        assert con.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?", (PROFIL_VERSION,)
        ).fetchone()[0] == 0
        assert not [r for r in con.execute("SELECT name FROM sqlite_master WHERE name LIKE '%_neu'")]
    finally:
        con.close()

    # Danach läuft die unveränderte Migration sauber durch.
    with TrackDB(pfad) as db:
        assert db.db.execute("SELECT COUNT(*) FROM tracks WHERE profile_id = 'schranz'").fetchone()[0] == 3


def test_upserts_schreiben_in_das_profil_der_trackdb(tmp_path):
    """Dieselbe sc_id bzw. derselbe Tag in zwei Profilen: je eine eigene Zeile, kein Überschreiben."""
    pfad = tmp_path / "t.sqlite"
    jetzt = datetime(2026, 11, 16, 7, 0, tzinfo=timezone.utc)
    with TrackDB(pfad) as db:
        db.upsert_store_item(4711, "Schranz-Titel", "A", "https://example.test/s")
        db.upsert_gate_item(811, "Schranz-Gate", "A", "https://example.test/gs")
        db.record_exploration_use("tribal", True, now=jetzt)
    with TrackDB(pfad, profile_id="house") as db:
        db.upsert_store_item(4711, "House-Titel", "B", "https://example.test/h")
        db.upsert_gate_item(811, "House-Gate", "B", "https://example.test/gh")
        db.record_exploration_use("tribal", False, now=jetzt)
        db.record_exploration_use("tribal", False, now=jetzt)
        db.upsert_store_item(4711, "House-Titel 2", "B", "https://example.test/h")

        assert _zeilen(db, "SELECT profile_id, title FROM store_items WHERE sc_id = 4711 "
                           "ORDER BY profile_id") == [("house", "House-Titel 2"), ("schranz", "Schranz-Titel")]
        assert _zeilen(db, "SELECT profile_id, title FROM gate_items WHERE sc_id = 811 "
                           "ORDER BY profile_id") == [("house", "House-Gate"), ("schranz", "Schranz-Gate")]
        assert _zeilen(db, "SELECT profile_id, uses, successes FROM exploration_tag_stats "
                           "WHERE tag = 'tribal' ORDER BY profile_id") == [("house", 2, 0), ("schranz", 1, 1)]
