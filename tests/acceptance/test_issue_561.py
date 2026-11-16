"""Akzeptanztests: Profil-Namensraum im Track-DB-Schema (Migration 0019)."""
import sqlite3

import pytest

from sc_digger import db as db_mod
from sc_digger.db import DEFAULT_PROFILE, MIGRATIONS, TrackDB, check_migrations


def _columns(conn: sqlite3.Connection, tabelle: str) -> dict[str, sqlite3.Row]:
    return {row["name"]: row for row in conn.execute(f"PRAGMA table_info({tabelle})")}


def test_standardprofil_ist_schranz():
    assert DEFAULT_PROFILE == "schranz"


def test_migrationsliste_bleibt_gueltig_und_kennt_version_19():
    assert check_migrations() == []
    assert max(version for version, _, _ in MIGRATIONS) == 19
    assert [name for version, name, _ in MIGRATIONS if version == 19][0].startswith("0019_")


def test_trackdb_kennt_sein_profil(tmp_path):
    with TrackDB(tmp_path / "a.sqlite") as db:
        assert db.profile_id == "schranz"
    with TrackDB(tmp_path / "b.sqlite", profile_id="house") as db:
        assert db.profile_id == "house"


def test_tracks_hat_profilspalte_mit_vorgabe_schranz(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        spalte = _columns(db.db, "tracks")["profile_id"]
        assert spalte["notnull"] == 1
        assert spalte["dflt_value"] in ("'schranz'", "schranz")


def test_taste_model_haelt_ein_modell_je_profil(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        assert "profile_id" in _columns(db.db, "taste_model")
        einfg = ("INSERT INTO taste_model "
                 "(profile_id, embedding_model, trained_at, n_pos, n_neg, model_json) "
                 "VALUES (?, ?, ?, ?, ?, ?)")
        db.db.execute(einfg, ("schranz", "mfcc-stat-v1", "2026-11-16 10:00:00", 10, 10, "{}"))
        db.db.execute(einfg, ("house", "mfcc-stat-v1", "2026-11-16 11:00:00", 3, 4, "{}"))
        assert db.db.execute("SELECT COUNT(*) FROM taste_model").fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError):
            db.db.execute(einfg, ("schranz", "mfcc-stat-v1", "2026-11-16 12:00:00", 1, 1, "{}"))


def test_taste_modell_roundtrip_nach_migration(tmp_path):
    from sc_digger.taste import TasteModel, load_taste_model, save_taste_model

    modell = TasteModel(weights=[1.0, 0.0], bias=0.0, mean=[0.0, 0.0], std=[1.0, 1.0])
    with TrackDB(tmp_path / "t.sqlite", profile_id="house") as db:
        save_taste_model(db, modell, "mfcc-stat-v1", n_pos=3, n_neg=4,
                         trained_at="2026-11-16 10:00:00")
        geladen = load_taste_model(db)
        assert geladen is not None
        assert geladen[0] == "mfcc-stat-v1"
        assert db.db.execute("SELECT profile_id FROM taste_model").fetchone()[0] == "house"


def test_exploration_tags_sind_je_profil_getrennt(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        db.db.execute("INSERT INTO exploration_tag_stats (tag, uses, successes) VALUES ('tribal', 1, 0)")
        db.db.execute("INSERT INTO exploration_tag_stats (profile_id, tag, uses, successes) "
                      "VALUES ('house', 'tribal', 2, 1)")
        assert db.db.execute("SELECT COUNT(*) FROM exploration_tag_stats").fetchone()[0] == 2


def test_store_und_gate_items_sind_je_profil_eindeutig(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        db.db.execute("INSERT INTO store_items (sc_id, title, artist, purchase_url) "
                      "VALUES (4711, 'T', 'A', 'https://example.test/x')")
        db.db.execute("INSERT INTO store_items (profile_id, sc_id, title, artist, purchase_url) "
                      "VALUES ('house', 4711, 'T', 'A', 'https://example.test/x')")
        db.db.execute("INSERT INTO gate_items (sc_id, title, artist, gate_url) "
                      "VALUES (4711, 'T', 'A', 'https://example.test/g')")
        db.db.execute("INSERT INTO gate_items (profile_id, sc_id, title, artist, gate_url) "
                      "VALUES ('house', 4711, 'T', 'A', 'https://example.test/g')")
        assert db.db.execute("SELECT COUNT(*) FROM store_items").fetchone()[0] == 2
        assert db.db.execute("SELECT COUNT(*) FROM gate_items").fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError):
            db.db.execute("INSERT INTO store_items (sc_id, title, artist, purchase_url) "
                          "VALUES (4711, 'T', 'A', 'https://example.test/y')")


def test_bestandsdaten_bekommen_das_standardprofil(tmp_path, monkeypatch):
    pfad = tmp_path / "alt.sqlite"
    monkeypatch.setattr(db_mod, "MIGRATIONS", [m for m in MIGRATIONS if m[0] <= 18])
    with TrackDB(pfad) as db:
        db.db.execute("INSERT INTO tracks (path, mtime, size, status) VALUES ('/x/a.flac', 1.0, 2, 'inbox')")
        db.db.execute("INSERT INTO taste_model (id, embedding_model, trained_at, n_pos, n_neg, model_json) "
                      "VALUES (1, 'mfcc-stat-v1', '2026-11-01 07:05:00', 7, 5, '{}')")
        db.db.execute("INSERT INTO store_items (sc_id, title, artist, purchase_url) "
                      "VALUES (4711, 'T', 'A', 'https://example.test/x')")
        db.db.execute("INSERT INTO gate_items (sc_id, title, artist, gate_url) "
                      "VALUES (4711, 'T', 'A', 'https://example.test/g')")
        db.db.execute("INSERT INTO exploration_tag_stats (tag, uses, successes) VALUES ('tribal', 3, 2)")
    monkeypatch.undo()

    with TrackDB(pfad) as db:
        assert db.db.execute("SELECT profile_id FROM tracks").fetchone()[0] == "schranz"
        assert db.db.execute("SELECT profile_id FROM taste_model").fetchone()[0] == "schranz"
        assert tuple(db.db.execute("SELECT profile_id, sc_id FROM store_items").fetchone()) == ("schranz", 4711)
        assert tuple(db.db.execute("SELECT profile_id, sc_id FROM gate_items").fetchone()) == ("schranz", 4711)
        assert tuple(db.db.execute("SELECT profile_id, tag, uses FROM exploration_tag_stats").fetchone()) == (
            "schranz", "tribal", 3)
        # das Modell-JSON und die Zähler bleiben unangetastet
        zeile = db.db.execute("SELECT embedding_model, n_pos, n_neg FROM taste_model").fetchone()
        assert tuple(zeile) == ("mfcc-stat-v1", 7, 5)


def test_migration_ist_wiederholbar(tmp_path):
    pfad = tmp_path / "t.sqlite"
    with TrackDB(pfad) as db:
        assert db.db.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = 19").fetchone()[0] == 1
    with TrackDB(pfad) as db:
        assert db.db.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = 19").fetchone()[0] == 1
