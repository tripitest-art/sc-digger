"""Eigene Tests zu Issue #565: Profile aus der Konfiguration und ihre Sicht auf die Track-DB."""
from pathlib import Path

import pytest
import yaml

from sc_digger import db as db_mod
from sc_digger.db import MIGRATIONS, TrackDB, TrackRecord, check_migrations
from sc_digger.profiles import (DEFAULT_PROFILE_NAME, Profile, ProfileError,
                                get_profile, load_profiles)

SEARCH = {"bpm_min": 150, "bpm_max": 165, "tags": ["schranz", "hardtechno"],
          "reference_accounts": ["https://soundcloud.com/a"]}


# ------------------------------------------------------------ load_profiles / get_profile

def test_standardname_passt_zur_spaltenvorgabe_der_track_db():
    assert DEFAULT_PROFILE_NAME == db_mod.DEFAULT_PROFILE


def test_ohne_block_erbt_das_standardprofil_alles_aus_search():
    assert load_profiles({"search": SEARCH}) == {
        "schranz": Profile(name="schranz", bpm_min=150.0, bpm_max=165.0,
                           tags=("schranz", "hardtechno"),
                           reference_accounts=("https://soundcloud.com/a",),
                           default_genre="Schranz"),
    }


def test_profil_nur_mit_tags_erbt_bpm_und_accounts():
    profile = load_profiles({"search": SEARCH, "profiles": {"acid": {"tags": ["acid techno"]}}})
    assert list(profile) == ["schranz", "acid"]      # Standardprofil immer vorn
    assert profile["acid"] == Profile(name="acid", bpm_min=150.0, bpm_max=165.0,
                                      tags=("acid techno",),
                                      reference_accounts=("https://soundcloud.com/a",))
    assert profile["schranz"].tags == ("schranz", "hardtechno")   # bleibt unberührt


def test_profil_mit_reference_accounts_ersetzt_die_geerbten():
    cfg = {"search": SEARCH,
           "profiles": {"house": {"bpm_min": 120, "bpm_max": 128,
                                  "reference_accounts": ["https://soundcloud.com/h"],
                                  "default_genre": "House"}}}
    house = get_profile(cfg, "house")
    assert house.reference_accounts == ("https://soundcloud.com/h",)
    assert house.tags == ("schranz", "hardtechno")   # nicht genannt, also geerbt
    assert (house.bpm_min, house.bpm_max, house.default_genre) == (120.0, 128.0, "House")
    assert get_profile(cfg, "schranz").reference_accounts == ("https://soundcloud.com/a",)


def test_leere_liste_ueberschreibt_none_erbt():
    cfg = {"search": SEARCH,
           "profiles": {"leer": {"reference_accounts": []}, "erbt": {"tags": None}, "nix": None}}
    profile = load_profiles(cfg)
    assert profile["leer"].reference_accounts == ()
    assert profile["erbt"].tags == ("schranz", "hardtechno")
    assert profile["nix"] == Profile(**{**profile["schranz"].__dict__, "name": "nix"})


def test_standardprofil_darf_im_block_ueberschrieben_werden():
    profile = load_profiles({"search": SEARCH, "profiles": {"schranz": {"bpm_max": 170}}})
    assert set(profile) == {"schranz"}
    assert (profile["schranz"].bpm_min, profile["schranz"].bpm_max) == (150.0, 170.0)


def test_bpm_min_ueber_bpm_max_ist_ein_profilfehler_mit_namen():
    with pytest.raises(ProfileError) as fehler:
        load_profiles({"search": SEARCH, "profiles": {"house": {"bpm_min": 130, "bpm_max": 120}}})
    assert "'house'" in str(fehler.value)
    assert "bpm_min" in str(fehler.value) and "bpm_max" in str(fehler.value)


def test_geerbte_grenze_zaehlt_beim_vergleich_mit():
    # bpm_max 140 allein liegt unter dem geerbten bpm_min 150.
    with pytest.raises(ProfileError, match="'trance'"):
        load_profiles({"search": SEARCH, "profiles": {"trance": {"bpm_max": 140}}})


def test_gleiche_grenzen_sind_erlaubt():
    profil = get_profile({"profiles": {"fix": {"bpm_min": 140, "bpm_max": 140}}}, "fix")
    assert profil.bpm_min == profil.bpm_max == 140.0


@pytest.mark.parametrize("block, stichwort", [
    ({"bpm_min": "schnell"}, "bpm_min"),
    ({"bpm_max": True}, "bpm_max"),
    ({"bpm_min": 0}, "bpm_min"),
    ({"bpm_max": float("nan")}, "bpm_max"),
    ({"tags": "house"}, "tags"),               # einzelner Text statt Liste
    ({"tags": ["house", 5]}, "tags"),
    ({"reference_accounts": {"a": 1}}, "reference_accounts"),
    ({"default_genre": ""}, "default_genre"),
    ({"bmp_min": 120}, "bmp_min"),             # Tippfehler im Schlüssel
])
def test_ungueltiger_block_nennt_profil_und_schluessel(block, stichwort):
    with pytest.raises(ProfileError) as fehler:
        load_profiles({"search": SEARCH, "profiles": {"house": block}})
    assert "'house'" in str(fehler.value)
    assert stichwort in str(fehler.value)


@pytest.mark.parametrize("profiles", [["house"], "house", {"house": ["tags"]}, {140: {}},
                                      {" house": {}}, {"": {}}])
def test_falsche_form_des_abschnitts_ist_ein_profilfehler(profiles):
    with pytest.raises(ProfileError):
        load_profiles({"search": SEARCH, "profiles": profiles})


def test_ungueltige_vorgabe_aus_search_nennt_das_standardprofil():
    with pytest.raises(ProfileError, match="'schranz'"):
        load_profiles({"search": {"bpm_min": 170, "bpm_max": 160}})


def test_profilfehler_ist_ein_valueerror_und_nennt_vorhandene_profile():
    assert issubclass(ProfileError, ValueError)
    with pytest.raises(ProfileError) as fehler:
        get_profile({"profiles": {"house": {}}}, "tekno")
    assert "'tekno'" in str(fehler.value)
    assert "house" in str(fehler.value) and "schranz" in str(fehler.value)


def test_mitgelieferte_config_hat_nur_das_standardprofil_mit_den_search_werten():
    wurzel = Path(__file__).resolve().parent.parent
    text = (wurzel / "config.yaml").read_text(encoding="utf-8")
    cfg = yaml.safe_load(text)
    # Der Beispielblock ist auskommentiert: es gibt ihn im Text, aber nicht als Schlüssel.
    assert "# profiles:" in text
    assert "profiles" not in cfg
    profile = load_profiles(cfg)
    assert set(profile) == {"schranz"}
    schranz = profile["schranz"]
    assert (schranz.bpm_min, schranz.bpm_max) == (cfg["search"]["bpm_min"], cfg["search"]["bpm_max"])
    assert schranz.tags == tuple(cfg["search"]["tags"])
    assert schranz.reference_accounts == tuple(cfg["search"]["reference_accounts"])


# ------------------------------------------------------------ Track-DB: Schreiben und Lesen

def _profil_der_zeile(db: TrackDB, tabelle: str) -> list[str]:
    return [r[0] for r in db.db.execute(f"SELECT profile_id FROM {tabelle} ORDER BY rowid")]


def test_upsert_track_schreibt_das_profil_der_instanz(tmp_path):
    pfad = tmp_path / "t.sqlite"
    with TrackDB(pfad) as db:
        db.upsert_track(TrackRecord(path="/x/a.flac", mtime=1.0, size=2))
    with TrackDB(pfad, profile_id="house") as db:
        db.upsert_track(TrackRecord(path="/x/b.flac", mtime=1.0, size=2))
        assert _profil_der_zeile(db, "tracks") == ["schranz", "house"]


def test_upsert_aus_fremdem_profil_zieht_den_track_nicht_um(tmp_path):
    pfad = tmp_path / "t.sqlite"
    with TrackDB(pfad) as db:
        db.upsert_track(TrackRecord(path="/x/a.flac", mtime=1.0, size=2))
    with TrackDB(pfad, profile_id="house") as db:
        db.upsert_track(TrackRecord(path="/x/a.flac", mtime=2.0, size=3))
        assert _profil_der_zeile(db, "tracks") == ["schranz"]
        assert db.list_tracks() == []
    with TrackDB(pfad) as db:
        assert [t.path for t in db.list_tracks()] == ["/x/a.flac"]


def test_list_tracks_filtert_profil_zusaetzlich_zu_status_und_limit(tmp_path):
    pfad = tmp_path / "t.sqlite"
    with TrackDB(pfad, profile_id="house") as db:
        db.upsert_track(TrackRecord(path="/x/h.flac", mtime=1.0, size=2, status="inbox"))
    with TrackDB(pfad) as db:
        for name, status in (("a", "inbox"), ("b", "archive"), ("c", "inbox")):
            db.upsert_track(TrackRecord(path=f"/x/{name}.flac", mtime=1.0, size=2, status=status))
        assert [t.path for t in db.list_tracks(status="inbox")] == ["/x/a.flac", "/x/c.flac"]
        assert [t.path for t in db.list_tracks(limit=2, offset=1)] == ["/x/b.flac", "/x/c.flac"]


def test_kaufliste_filter_gilt_auch_mit_only_liked_und_since_days(tmp_path):
    pfad = tmp_path / "t.sqlite"
    with TrackDB(pfad, profile_id="house") as db:
        db.upsert_store_item(1, "H", "A", "https://example.test/h")
        db.set_sc_feedback(1, "like")
    with TrackDB(pfad) as db:
        db.upsert_store_item(1, "S", "A", "https://example.test/s")   # gleiche sc_id, anderes Profil
        assert [i["title"] for i in db.get_store_items()] == ["S"]
        assert [i["title"] for i in db.get_store_items(only_liked=True, since_days=7)] == ["S"]
        assert _profil_der_zeile(db, "store_items") == ["house", "schranz"]


def test_gates_und_explorations_tags_tragen_das_profil(tmp_path):
    pfad = tmp_path / "t.sqlite"
    with TrackDB(pfad, profile_id="house") as db:
        db.upsert_gate_item(7, "H", "A", "https://example.test/g")
        db.record_exploration_use("deep house", True)
        assert _profil_der_zeile(db, "gate_items") == ["house"]
        assert _profil_der_zeile(db, "exploration_tag_stats") == ["house"]
        assert [g["sc_id"] for g in db.get_gate_items()] == [7]
    with TrackDB(pfad) as db:
        assert db.get_gate_items() == []


# ------------------------------------------------------------ Migration 0023

def test_migration_ist_die_juengste_und_die_liste_bleibt_gueltig():
    assert check_migrations() == []
    # Nicht „die jüngste“ festschreiben (wie test_issue_563): jede spätere Migration, z. B.
    # 0024_activity_log aus #619, machte den Test sonst ohne Grund rot.
    assert (23, "0023_smartlists_profile") in {(v, n) for v, n, _ in MIGRATIONS}


def test_migration_uebernimmt_bestandsdaten_ins_standardprofil(tmp_path, monkeypatch):
    pfad = tmp_path / "t.sqlite"
    # Stand vor dieser Aufgabe herstellen: nur die Migrationen bis 0022.
    monkeypatch.setattr(db_mod, "MIGRATIONS", [m for m in MIGRATIONS if m[0] <= 22])
    with TrackDB(pfad) as db:
        db.db.execute(
            "INSERT INTO smartlists (name, rules_json, created_at, updated_at) "
            "VALUES ('peak', '{\"bpm_min\": 150.0}', '2026-01-02 03:04:05', '2026-02-03 04:05:06')"
        )
        db.db.commit()
    monkeypatch.undo()

    with TrackDB(pfad) as db:
        zeilen = [tuple(r) for r in db.db.execute(
            "SELECT profile_id, name, rules_json, created_at, updated_at FROM smartlists")]
        assert zeilen == [("schranz", "peak", '{"bpm_min": 150.0}',
                           "2026-01-02 03:04:05", "2026-02-03 04:05:06")]
        assert db.get_smartlist("peak") == {"bpm_min": 150.0}
        assert db.db.execute(
            "SELECT name FROM sqlite_master WHERE name = 'smartlists_neu'").fetchone() is None
        assert db.db.execute(
            "SELECT name FROM schema_migrations WHERE version = 23"
        ).fetchone()[0] == "0023_smartlists_profile"
