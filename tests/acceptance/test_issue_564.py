"""Akzeptanztests: Smartlist-Regeln."""
import pytest

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.smartlists import RuleError, Rules, matches, parse_rules, select_tracks


def _row(**werte):
    basis = {"id": 1, "bpm": None, "energy": None, "key_camelot": None, "taste_score": None,
             "rb_rating": None, "duration": None}
    basis.update(werte)
    return basis


def test_leere_regeln_passen_immer():
    rules = parse_rules({})
    assert rules == Rules()
    assert matches(_row(), rules) is True


def test_unbekannter_schluessel_nennt_sich_in_der_meldung():
    with pytest.raises(RuleError) as fehler:
        parse_rules({"bpm_min": 150, "farbe": "rot"})
    assert "farbe" in str(fehler.value)


def test_widerspruechliche_grenzen_werden_abgelehnt():
    with pytest.raises(RuleError) as fehler:
        parse_rules({"bpm_min": 170, "bpm_max": 150})
    assert "bpm" in str(fehler.value)


def test_bpm_fenster_ist_einschliesslich_und_braucht_einen_wert():
    rules = parse_rules({"bpm_min": 150, "bpm_max": 160})
    assert matches(_row(bpm=150.0), rules) is True
    assert matches(_row(bpm=160.0), rules) is True
    assert matches(_row(bpm=161.0), rules) is False
    assert matches(_row(bpm=None), rules) is False


def test_key_energie_taste_und_rating():
    rules = parse_rules({"keys": ["8A", "9A"], "energy_min": 4, "taste_min": 0.7, "rating_min": 4})
    assert matches(_row(key_camelot="8A", energy=5, taste_score=0.81, rb_rating=5), rules) is True
    assert matches(_row(key_camelot="8B", energy=5, taste_score=0.81, rb_rating=5), rules) is False
    assert matches(_row(key_camelot="8A", energy=3, taste_score=0.81, rb_rating=5), rules) is False
    assert matches(_row(key_camelot="8A", energy=5, taste_score=0.69, rb_rating=5), rules) is False
    assert matches(_row(key_camelot="8A", energy=5, taste_score=0.81, rb_rating=3), rules) is False
    assert matches(_row(key_camelot="8A", energy=5, taste_score=None, rb_rating=5), rules) is False


def test_tags_all_und_tags_any():
    alle = parse_rules({"tags_all": ["peak time", "opener"]})
    assert matches(_row(), alle, ["opener", "peak time"]) is True
    assert matches(_row(), alle, ["opener"]) is False
    eins = parse_rules({"tags_any": ["peak time", "warmup"]})
    assert matches(_row(), eins, ["warmup"]) is True
    assert matches(_row(), eins, ["opener"]) is False


def test_tabellenrunde(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        db.save_smartlist("peak", {"bpm_min": 150, "tags_any": ["Peak Time"]})
        db.save_smartlist("warmup", {"bpm_max": 145})
        gespeichert = db.get_smartlist("peak")
        assert gespeichert is not None and gespeichert["bpm_min"] == 150
        assert gespeichert["tags_any"] == ["peak time"]
        assert db.get_smartlist("gibtsnicht") is None
        assert [name for name, _ in db.list_smartlists()] == ["peak", "warmup"]


def test_select_tracks_filtert_und_sortiert(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        for pfad, bpm in (("/x/c.flac", 160.0), ("/x/a.flac", 150.0), ("/x/b.flac", 155.0), ("/x/d.flac", None)):
            t = db.upsert_track(TrackRecord(path=pfad, mtime=1.0, size=2, bpm=bpm))
            assert t.id is not None
        rules = parse_rules({"bpm_min": 150, "bpm_max": 160})
        treffer = select_tracks(db, rules)
        assert [t.path.split("/")[-1] for t in treffer] == ["a.flac", "b.flac", "c.flac"]
