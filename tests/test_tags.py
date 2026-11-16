"""Eigene Tests zu freien Tags (Issue #563): Normalisierung und TrackDB-Methoden."""
import sqlite3
import unicodedata

import pytest

from sc_digger.db import MIGRATIONS, TrackDB, TrackRecord, check_migrations
from sc_digger.tags import TAG_MAX_LEN, normalize_tag


def _track(db: TrackDB, pfad: str) -> TrackRecord:
    return db.upsert_track(TrackRecord(path=pfad, mtime=1.0, size=2))


# ------------------------------------------------------------ normalize_tag

@pytest.mark.parametrize(
    ("roh", "erwartet"),
    [
        ("Übergang", "übergang"),
        ("ÄÖÜ", "äöü"),
        ("Groß", "groß"),
        ("Stoß-Dämpfer_2", "stoß-dämpfer_2"),
    ],
)
def test_umlaute_und_eszett_bleiben_erhalten(roh, erwartet):
    assert normalize_tag(roh) == erwartet


def test_zerlegter_umlaut_wird_zusammengesetzt():
    # macOS-Dateinamen liefern „a“ + kombinierendes Trema; das ist derselbe Tag wie „ä“.
    zerlegt = unicodedata.normalize("NFD", "Käfig")
    assert zerlegt != "Käfig"
    assert normalize_tag(zerlegt) == "käfig"


@pytest.mark.parametrize(
    "roh",
    ["  opener", "opener   ", "\topener\n", "  opener  ", " opener "],
)
def test_leerraum_am_rand_wird_entfernt(roh):
    assert normalize_tag(roh) == "opener"


def test_innerer_leerraum_wird_zu_einem_leerzeichen():
    assert normalize_tag("peak \t\n  time") == "peak time"


def test_laenge_zaehlt_nach_der_normalisierung():
    # Rand-Leerraum zählt nicht mit, innere Leerzeichen schon.
    assert normalize_tag("  " + "x" * TAG_MAX_LEN + "  ") == "x" * TAG_MAX_LEN
    with pytest.raises(ValueError):
        normalize_tag("x" * TAG_MAX_LEN + " y")


@pytest.mark.parametrize("roh", ["é", "ñandú", "tag,komma", "a.b", "a+b", "tag#1", "🔥", "ÿ"])
def test_fremde_zeichen_werden_abgelehnt(roh):
    with pytest.raises(ValueError):
        normalize_tag(roh)


@pytest.mark.parametrize("roh", [None, 5, b"opener"])
def test_nicht_text_ist_valueerror(roh):
    with pytest.raises(ValueError):
        normalize_tag(roh)


# ------------------------------------------------------------ Migration

def test_migrationsliste_bleibt_gueltig():
    assert check_migrations() == []
    assert (21, "0021_track_tags") in [(v, n) for v, n, _ in MIGRATIONS]


def test_schema_der_tabelle(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        spalten = {r["name"]: r for r in db.db.execute("PRAGMA table_info(track_tags)")}
        assert set(spalten) == {"track_id", "tag", "created_at"}
        assert all(r["notnull"] == 1 for r in spalten.values())
        assert {n: r["pk"] for n, r in spalten.items()} == {"track_id": 1, "tag": 2, "created_at": 0}
        fks = db.db.execute("PRAGMA foreign_key_list(track_tags)").fetchall()
        assert [(f["table"], f["from"], f["to"], f["on_delete"]) for f in fks] == [
            ("tracks", "track_id", "id", "CASCADE")
        ]
        index = db.db.execute("PRAGMA index_info(idx_track_tags_tag)").fetchall()
        assert [i["name"] for i in index] == ["tag"]


# ------------------------------------------------------------ TrackDB

def test_doppeltes_hinzufuegen_legt_nur_eine_zeile_an(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        a = _track(db, "/x/a.flac")
        assert db.add_track_tag(a.id, "Übergang") == "übergang"
        erst = db.db.execute("SELECT created_at FROM track_tags").fetchone()[0]
        # Andere Schreibweise, derselbe Tag: keine zweite Zeile, Zeitstempel bleibt.
        assert db.add_track_tag(a.id, "  ÜBERGANG ") == "übergang"
        zeilen = db.db.execute("SELECT track_id, tag, created_at FROM track_tags").fetchall()
        assert [tuple(z) for z in zeilen] == [(a.id, "übergang", erst)]
        assert db.list_tags() == [("übergang", 1)]


def test_unbekannter_track_ist_integrityerror_und_hinterlaesst_nichts(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        a = _track(db, "/x/a.flac")
        with pytest.raises(sqlite3.IntegrityError):
            db.add_track_tag(a.id + 1000, "opener")
        assert db.db.execute("SELECT COUNT(*) FROM track_tags").fetchone()[0] == 0
        # Die Verbindung bleibt benutzbar (keine hängende Transaktion).
        assert not db.db.in_transaction
        assert db.add_track_tag(a.id, "opener") == "opener"


def test_ungueltiger_tag_schreibt_nichts(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        a = _track(db, "/x/a.flac")
        with pytest.raises(ValueError):
            db.add_track_tag(a.id, "Opener!")
        assert db.get_track_tags(a.id) == []


def test_entfernen_und_suchen_normalisieren_die_eingabe(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        a = _track(db, "/x/a.flac")
        b = _track(db, "/x/b.flac")
        db.add_track_tag(a.id, "peak time")
        db.add_track_tag(b.id, "peak time")
        assert db.list_track_ids_by_tag("  Peak   TIME ") == [a.id, b.id]
        assert db.remove_track_tag(a.id, " PEAK time") is True
        assert db.list_track_ids_by_tag("peak time") == [b.id]
        # Entfernen wirkt nur auf den genannten Track.
        assert db.get_track_tags(b.id) == ["peak time"]


def test_tags_eines_tracks_sind_alphabetisch(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        a = _track(db, "/x/a.flac")
        for tag in ("warmup", "opener", "fake-drop"):
            db.add_track_tag(a.id, tag)
        assert db.get_track_tags(a.id) == ["fake-drop", "opener", "warmup"]


def test_leere_datenbank_liefert_leere_listen(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        assert db.list_tags() == []
        assert db.get_track_tags(1) == []
        assert db.list_track_ids_by_tag("opener") == []
        assert db.remove_track_tag(1, "opener") is False


def test_tags_ueberleben_das_erneute_oeffnen(tmp_path):
    pfad = tmp_path / "t.sqlite"
    with TrackDB(pfad) as db:
        a = _track(db, "/x/a.flac")
        db.add_track_tag(a.id, "opener")
    with TrackDB(pfad) as db:
        assert db.get_track_tags(a.id) == ["opener"]
