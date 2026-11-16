"""Eigene Tests zu Smartlist-Regeln (Issue #564): Regeln prüfen, auswerten, speichern, auswählen."""
import json
import sqlite3

import pytest

from sc_digger.db import MIGRATIONS, TrackDB, TrackRecord, check_migrations
from sc_digger.smartlists import RuleError, Rules, matches, parse_rules, select_tracks


def _row(**werte):
    basis = {"id": 1, "bpm": None, "energy": None, "key_camelot": None, "taste_score": None,
             "rb_rating": None, "duration": None}
    basis.update(werte)
    return basis


def _track(db: TrackDB, name: str, *, taste: float | None = None, **werte) -> TrackRecord:
    t = db.upsert_track(TrackRecord(path=f"/x/{name}.flac", mtime=1.0, size=2, **werte))
    if taste is not None:
        # taste_score ist kein Feld von TrackRecord; der Wert kommt sonst aus dem Taste-Modell.
        db.db.execute("UPDATE tracks SET taste_score = ? WHERE id = ?", (taste, t.id))
        db.db.commit()
    return t


def _namen(tracks: list[TrackRecord]) -> list[str]:
    return [t.path.split("/")[-1].removesuffix(".flac") for t in tracks]


# ------------------------------------------------------------ parse_rules

def test_leere_regeln_sind_der_standard_und_passen_auf_alles():
    rules = parse_rules({})
    assert rules == Rules()
    assert matches(_row(), rules) is True
    assert matches(_row(), rules, ["opener"]) is True
    # Auch eine Zeile ganz ohne die Spalten passt: keine Regel fragt danach.
    assert matches({}, rules) is True


def test_none_und_leere_listen_gelten_als_nicht_gesetzt():
    rules = parse_rules({"bpm_min": None, "keys": [], "tags_all": [], "tags_any": None})
    assert rules == Rules()


def test_werte_werden_in_die_typen_der_regeln_gebracht():
    rules = parse_rules({
        "bpm_min": 150, "bpm_max": 160.5, "keys": ["8a", " 9A ", "8A"],
        "energy_min": 4, "energy_max": 5, "taste_min": 0.7, "rating_min": 4,
        "duration_min": 180, "duration_max": 420,
        "tags_all": ["Peak  Time", "peak time", "Opener"], "tags_any": ("WARMUP",),
    })
    assert rules == Rules(
        bpm_min=150.0, bpm_max=160.5, keys=frozenset({"8A", "9A"}),
        energy_min=4, energy_max=5, taste_min=0.7, rating_min=4,
        duration_min=180.0, duration_max=420.0,
        tags_all=("peak time", "opener"), tags_any=("warmup",),
    )
    assert isinstance(rules.bpm_min, float) and isinstance(rules.duration_max, float)


@pytest.mark.parametrize(
    ("daten", "schluessel"),
    [
        ({"bpm_min": "150"}, "bpm_min"),
        ({"bpm_max": True}, "bpm_max"),
        ({"bpm_min": float("nan")}, "bpm_min"),
        ({"taste_min": [0.7]}, "taste_min"),
        ({"energy_min": 4.0}, "energy_min"),
        ({"energy_max": False}, "energy_max"),
        ({"rating_min": "4"}, "rating_min"),
        ({"duration_max": "7 Minuten"}, "duration_max"),
        ({"keys": "8A"}, "keys"),
        ({"keys": [8]}, "keys"),
        ({"keys": ["8A", " "]}, "keys"),
        ({"tags_all": "peak time"}, "tags_all"),
        ({"tags_any": [1, 2]}, "tags_any"),
        ({"tags_any": ["Peak!"]}, "tags_any"),
        ({"tags_all": [""]}, "tags_all"),
    ],
)
def test_falscher_typ_nennt_den_schluessel(daten, schluessel):
    with pytest.raises(RuleError) as fehler:
        parse_rules(daten)
    assert schluessel in str(fehler.value)


def test_regelfehler_ist_ein_valueerror():
    assert issubclass(RuleError, ValueError)
    with pytest.raises(RuleError):
        parse_rules(["bpm_min"])


@pytest.mark.parametrize(
    ("daten", "unten", "oben"),
    [
        ({"bpm_min": 160.5, "bpm_max": 160}, "bpm_min", "bpm_max"),
        ({"energy_min": 5, "energy_max": 4}, "energy_min", "energy_max"),
        ({"duration_min": 421, "duration_max": 420}, "duration_min", "duration_max"),
    ],
)
def test_min_ueber_max_nennt_beide_schluessel(daten, unten, oben):
    with pytest.raises(RuleError) as fehler:
        parse_rules(daten)
    assert unten in str(fehler.value) and oben in str(fehler.value)


def test_gleiche_grenzen_sind_erlaubt():
    rules = parse_rules({"bpm_min": 155, "bpm_max": 155, "energy_min": 3, "energy_max": 3})
    assert matches(_row(bpm=155.0, energy=3), rules) is True
    assert matches(_row(bpm=155.1, energy=3), rules) is False


# ------------------------------------------------------------ matches

def test_kombiniertes_regelwerk_braucht_jede_einzelne_regel():
    rules = parse_rules({
        "bpm_min": 150, "bpm_max": 160, "keys": ["8A", "9A", "8B"],
        "energy_min": 4, "energy_max": 5, "taste_min": 0.7, "rating_min": 4,
        "duration_max": 420, "tags_all": ["peak time"], "tags_any": ["opener", "closer"],
    })
    passend = {"bpm": 155.0, "key_camelot": "9A", "energy": 4, "taste_score": 0.7,
               "rb_rating": 4, "duration": 420.0}
    tags = ["closer", "peak time"]
    assert matches(_row(**passend), rules, tags) is True

    # Je ein Wert knapp daneben kippt das Ergebnis – sonst wäre eine Regel wirkungslos.
    daneben = {"bpm": 160.1, "key_camelot": "10A", "energy": 3, "taste_score": 0.699,
               "rb_rating": 3, "duration": 420.5}
    for spalte, wert in daneben.items():
        assert matches(_row(**{**passend, spalte: wert}), rules, tags) is False, spalte
        assert matches(_row(**{**passend, spalte: None}), rules, tags) is False, spalte
    assert matches(_row(**passend), rules, ["closer"]) is False       # tags_all fehlt
    assert matches(_row(**passend), rules, ["peak time"]) is False    # tags_any fehlt
    assert matches(_row(**passend), rules) is False


def test_obergrenzen_allein_und_untergrenzen_allein():
    assert matches(_row(energy=5), parse_rules({"energy_max": 4})) is False
    assert matches(_row(energy=4), parse_rules({"energy_max": 4})) is True
    assert matches(_row(duration=179.9), parse_rules({"duration_min": 180})) is False
    assert matches(_row(duration=180), parse_rules({"duration_min": 180})) is True
    assert matches(_row(bpm=140), parse_rules({"bpm_max": 145})) is True
    assert matches(_row(), parse_rules({"bpm_max": 145})) is False


def test_fehlende_spalte_zaehlt_wie_null():
    assert matches({"id": 1}, parse_rules({"bpm_min": 150})) is False
    assert matches({"id": 1}, parse_rules({"keys": ["8A"]})) is False


def test_key_vergleich_ignoriert_gross_klein_und_rand():
    rules = parse_rules({"keys": ["8a"]})
    assert matches(_row(key_camelot=" 8a "), rules) is True
    assert matches(_row(key_camelot="8B"), rules) is False


def test_matches_liest_auch_sqlite_zeilen(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        _track(db, "a", bpm=152.0, key_camelot="8A", taste=0.9)
        zeile = db.db.execute("SELECT * FROM tracks").fetchone()
        assert isinstance(zeile, sqlite3.Row)
        assert matches(zeile, parse_rules({"bpm_min": 150, "taste_min": 0.8, "keys": ["8A"]})) is True
        assert matches(zeile, parse_rules({"taste_min": 0.95})) is False


# ------------------------------------------------------------ select_tracks

def test_sortierung_bpm_dann_key_dann_id_und_none_zuletzt(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        _track(db, "ohne-bpm-1")
        _track(db, "schnell", bpm=160.0, key_camelot="1A")
        _track(db, "zehn", bpm=150.0, key_camelot="10A")
        _track(db, "zwei", bpm=150.0, key_camelot="2A")
        _track(db, "ohne-key", bpm=150.0)
        _track(db, "zwei-nochmal", bpm=150.0, key_camelot="2A")
        _track(db, "zwei-b", bpm=150.0, key_camelot="2B")
        _track(db, "ohne-bpm-2", key_camelot="1A")
        # Leere Regeln: alle Tracks. Bei gleichem BPM entscheidet der Key in Rad-Reihenfolge
        # (2A vor 10A), bei gleichem Key die id; fehlender Key und fehlendes BPM stehen hinten.
        assert _namen(select_tracks(db, Rules())) == [
            "zwei", "zwei-nochmal", "zwei-b", "zehn", "ohne-key", "schnell",
            "ohne-bpm-2", "ohne-bpm-1",
        ]


def test_limit_schneidet_nach_der_sortierung(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        for name, bpm in (("c", 160.0), ("a", 150.0), ("b", 155.0)):
            _track(db, name, bpm=bpm)
        assert _namen(select_tracks(db, Rules(), limit=2)) == ["a", "b"]
        assert select_tracks(db, Rules(), limit=0) == []
        assert _namen(select_tracks(db, Rules(), limit=99)) == ["a", "b", "c"]
        with pytest.raises(ValueError):
            select_tracks(db, Rules(), limit=-1)


def test_auswahl_liest_tags_aus_track_tags(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        a = _track(db, "a", bpm=150.0)
        b = _track(db, "b", bpm=151.0)
        c = _track(db, "c", bpm=152.0)
        _track(db, "ohne-tags", bpm=153.0)
        db.add_track_tag(a.id, "Peak Time")
        db.add_track_tag(a.id, "opener")
        db.add_track_tag(b.id, "peak time")
        db.add_track_tag(c.id, "warmup")
        assert _namen(select_tracks(db, parse_rules({"tags_all": ["PEAK TIME", "Opener"]}))) == ["a"]
        assert _namen(select_tracks(db, parse_rules({"tags_any": ["opener", "warmup"]}))) == ["a", "c"]
        assert _namen(select_tracks(db, parse_rules({"tags_all": ["peak time"], "bpm_min": 151}))) == ["b"]
        assert select_tracks(db, parse_rules({"tags_any": ["closer"]})) == []


def test_auswahl_mit_kombinierten_regeln_aus_der_datenbank(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        _track(db, "passt", bpm=155.0, key_camelot="8A", energy=5, rb_rating=5,
               duration=400.0, taste=0.9)
        _track(db, "zu-lang", bpm=155.0, key_camelot="8A", energy=5, rb_rating=5,
               duration=500.0, taste=0.9)
        _track(db, "kein-taste", bpm=155.0, key_camelot="8A", energy=5, rb_rating=5,
               duration=400.0)
        _track(db, "falscher-key", bpm=155.0, key_camelot="3B", energy=5, rb_rating=5,
               duration=400.0, taste=0.9)
        rules = parse_rules({"bpm_min": 150, "bpm_max": 160, "keys": ["8A", "9A"],
                             "energy_min": 4, "taste_min": 0.7, "rating_min": 4,
                             "duration_max": 420})
        treffer = select_tracks(db, rules)
        assert _namen(treffer) == ["passt"]
        assert isinstance(treffer[0], TrackRecord) and treffer[0].bpm == 155.0


def test_auswahl_auf_leerer_datenbank(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        assert select_tracks(db, Rules()) == []
        assert select_tracks(db, parse_rules({"tags_any": ["opener"]})) == []


# ------------------------------------------------------------ Tabelle smartlists

def test_migration_ist_eingetragen_und_die_liste_bleibt_gueltig(tmp_path):
    assert check_migrations() == []
    assert [name for _, name, _ in MIGRATIONS if name.endswith("_smartlists")] == ["0022_smartlists"]
    with TrackDB(tmp_path / "t.sqlite") as db:
        spalten = {r["name"]: (r["type"], r["notnull"], r["pk"])
                   for r in db.db.execute("PRAGMA table_info(smartlists)")}
        assert spalten == {
            "name": ("TEXT", 0, 1),
            "rules_json": ("TEXT", 1, 0),
            "created_at": ("TEXT", 1, 0),
            "updated_at": ("TEXT", 1, 0),
        }
        assert db.db.execute(
            "SELECT name FROM schema_migrations WHERE version = 22"
        ).fetchone()[0] == "0022_smartlists"


def test_gespeichertes_json_enthaelt_nur_gesetzte_normalisierte_regeln(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        db.save_smartlist("peak", {
            "bpm_min": 150, "bpm_max": None, "keys": ["9a", "8A"],
            "tags_all": ["  Peak   TIME ", "Übergang"], "tags_any": [],
        })
        roh = db.db.execute("SELECT rules_json FROM smartlists WHERE name = 'peak'").fetchone()[0]
        assert json.loads(roh) == {
            "bpm_min": 150.0, "keys": ["8A", "9A"], "tags_all": ["peak time", "übergang"],
        }
        # Umlaute stehen lesbar im JSON, nicht als \u-Folge.
        assert "übergang" in roh
        gespeichert = db.get_smartlist("peak")
        assert gespeichert == json.loads(roh)
        # Was gespeichert ist, lässt sich wieder als Regel lesen und ergibt dasselbe.
        assert parse_rules(gespeichert) == parse_rules({
            "bpm_min": 150, "keys": ["8A", "9A"], "tags_all": ["peak time", "übergang"],
        })


def test_speichern_ersetzt_und_behaelt_den_anlagezeitpunkt(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        db.save_smartlist("peak", {"bpm_min": 150, "tags_any": ["opener"]})
        db.db.execute("UPDATE smartlists SET created_at = '2020-01-01 00:00:00',"
                      " updated_at = '2020-01-01 00:00:00'")
        db.db.commit()
        db.save_smartlist("peak", {"bpm_max": 145})
        assert db.get_smartlist("peak") == {"bpm_max": 145.0}
        angelegt, geaendert = db.db.execute(
            "SELECT created_at, updated_at FROM smartlists WHERE name = 'peak'"
        ).fetchone()
        assert angelegt == "2020-01-01 00:00:00"
        assert geaendert > "2020-01-01 00:00:00"
        assert db.db.execute("SELECT COUNT(*) FROM smartlists").fetchone()[0] == 1


def test_ungueltige_regeln_und_leerer_name_schreiben_nichts(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        with pytest.raises(RuleError):
            db.save_smartlist("kaputt", {"farbe": "rot"})
        with pytest.raises(RuleError):
            db.save_smartlist("kaputt", {"bpm_min": 170, "bpm_max": 150})
        with pytest.raises(ValueError):
            db.save_smartlist("  ", {"bpm_min": 150})
        assert db.list_smartlists() == []
        assert db.get_smartlist("kaputt") is None


def test_liste_ist_alphabetisch_und_zaehlt_die_gesetzten_regeln(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        assert db.list_smartlists() == []
        db.save_smartlist("warmup", {"bpm_max": 145})
        db.save_smartlist("Peak", {"bpm_min": 150, "bpm_max": 160, "tags_any": ["peak time"]})
        db.save_smartlist("alles", {})
        db.save_smartlist("closer", {"keys": ["8A", "9A"], "energy_min": None})
        assert db.list_smartlists() == [("alles", 0), ("closer", 1), ("Peak", 3), ("warmup", 1)]


def test_smartlists_ueberleben_das_erneute_oeffnen(tmp_path):
    pfad = tmp_path / "t.sqlite"
    with TrackDB(pfad) as db:
        a = _track(db, "a", bpm=152.0)
        _track(db, "b", bpm=140.0)
        db.add_track_tag(a.id, "opener")
        db.save_smartlist("opener", {"bpm_min": 150, "tags_all": ["Opener"]})
    with TrackDB(pfad) as db:
        gespeichert = db.get_smartlist("opener")
        assert gespeichert == {"bpm_min": 150.0, "tags_all": ["opener"]}
        assert _namen(select_tracks(db, parse_rules(gespeichert))) == ["a"]
