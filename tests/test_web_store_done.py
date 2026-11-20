"""Eigene Tests: done_at in query_store_items und Umschalter der Kaufliste (#644)."""
from __future__ import annotations

from sc_digger.db import TrackDB
from sc_digger.web.database import query_store_items


def _db(tmp_path):
    pfad = tmp_path / "track.sqlite"
    with TrackDB(pfad) as db:
        db.upsert_store_item(1, "Alpha", "Eins", "https://store.example/a")
        db.upsert_store_item(2, "Beta", "Zwei", "https://store.example/b")
        db.mark_store_item_done(1, True)
    return pfad


def test_done_at_je_zeile_richtig(tmp_path):
    items = {i["sc_id"]: i for i in query_store_items(_db(tmp_path)).items}
    assert items[1]["done_at"]
    assert items[2]["done_at"] is None


def test_erledigte_zeile_bleibt_in_der_liste(tmp_path):
    assert query_store_items(_db(tmp_path)).total == 2


def test_suche_bleibt_unberuehrt(tmp_path):
    erg = query_store_items(_db(tmp_path), search="Beta")
    assert [i["sc_id"] for i in erg.items] == [2]
    assert erg.items[0]["done_at"] is None


def test_filter_feedback_none_bleibt_unberuehrt(tmp_path):
    erg = query_store_items(_db(tmp_path), feedback="none")
    assert erg.total == 2
