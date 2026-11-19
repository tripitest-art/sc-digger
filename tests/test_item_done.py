"""Eigene Tests der erledigt-Marke (Issue #638): DB-Methoden, Profil-Trennung, Migration."""
import sqlite3

from sc_digger.db import MIGRATIONS, TrackDB


def _done(path, tabelle, sc_id, profil="schranz"):
    with sqlite3.connect(path) as c:
        z = c.execute(f"SELECT done_at FROM {tabelle} WHERE sc_id=? AND profile_id=?", (sc_id, profil)).fetchone()
    return None if z is None else z[0]


def test_unbekannte_id_meldet_false_und_schreibt_nichts(tmp_path):
    p = tmp_path / "t.sqlite"
    with TrackDB(p) as db:
        db.upsert_gate_item(1, "T", "A", "https://g.example")
        db.upsert_store_item(2, "T", "A", "https://s.example")
        assert db.mark_gate_item_done(999) is False
        assert db.mark_store_item_done(999) is False
        # Ein Gate-sc_id trifft keinen Kauf und umgekehrt.
        assert db.mark_store_item_done(1) is False
        assert db.mark_gate_item_done(2) is False
    assert _done(p, "gate_items", 1) is None
    assert _done(p, "store_items", 2) is None


def test_zuruecknehmen_ohne_marke_bleibt_none(tmp_path):
    p = tmp_path / "t.sqlite"
    with TrackDB(p) as db:
        db.upsert_gate_item(1, "T", "A", "https://g.example")
        db.upsert_store_item(2, "T", "A", "https://s.example")
        assert db.mark_gate_item_done(1, done=False) is True
        assert db.mark_store_item_done(2, done=False) is True
    assert _done(p, "gate_items", 1) is None
    assert _done(p, "store_items", 2) is None


def test_kauf_wird_markiert_und_vom_upsert_geoeffnet(tmp_path):
    p = tmp_path / "t.sqlite"
    with TrackDB(p) as db:
        db.upsert_store_item(2, "T", "A", "https://s.example")
        assert db.mark_store_item_done(2) is True
        assert _done(p, "store_items", 2) is not None
        db.upsert_store_item(2, "T", "A", "https://s.example")
    assert _done(p, "store_items", 2) is None


def test_fremdes_profil_bleibt_unberuehrt(tmp_path):
    p = tmp_path / "t.sqlite"
    with TrackDB(p, profile_id="anderes") as db:
        db.upsert_gate_item(1, "T", "A", "https://g.example")
    with TrackDB(p) as db:
        assert db.mark_gate_item_done(1) is False
    assert _done(p, "gate_items", 1, "anderes") is None


def test_migration_25_ist_die_juengste():
    assert MIGRATIONS[-1][0] == 25
    assert MIGRATIONS[-1][1] == "0025_done_at"
