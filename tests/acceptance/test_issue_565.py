"""Akzeptanztests: Profil in Lese- und Schreibpfaden."""
import pytest

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.profiles import (DEFAULT_PROFILE_NAME, Profile, ProfileError,
                                get_profile, load_profiles)


def test_standardprofil_ohne_konfiguration():
    profile = load_profiles({})
    assert DEFAULT_PROFILE_NAME == "schranz"
    assert set(profile) == {"schranz"}
    assert profile["schranz"].bpm_min < profile["schranz"].bpm_max


def test_eigenes_profil_erbt_vorgaben_aus_search():
    cfg = {
        "search": {"bpm_min": 120, "bpm_max": 180, "tags": ["techno"],
                   "reference_accounts": ["dj-a"]},
        "profiles": {"house": {"bpm_min": 118, "bpm_max": 130, "tags": ["house"]}},
    }
    profile = load_profiles(cfg)
    assert set(profile) == {"schranz", "house"}
    assert profile["house"].bpm_min == 118
    assert profile["house"].tags == ("house",)
    assert profile["house"].reference_accounts == ("dj-a",)  # geerbt
    assert profile["schranz"].bpm_min == 120


def test_unbekanntes_profil_nennt_sich_in_der_meldung():
    with pytest.raises(ProfileError) as fehler:
        get_profile({}, "gibtsnicht")
    assert "gibtsnicht" in str(fehler.value)
    assert get_profile({}, "schranz").name == "schranz"


def test_tracks_werden_nach_profil_getrennt(tmp_path):
    pfad = tmp_path / "t.sqlite"
    with TrackDB(pfad, profile_id="schranz") as db:
        db.upsert_track(TrackRecord(path="/x/a.flac", mtime=1.0, size=2))
    with TrackDB(pfad, profile_id="house") as db:
        db.upsert_track(TrackRecord(path="/x/b.flac", mtime=1.0, size=2))
        assert [t.path for t in db.list_tracks()] == ["/x/b.flac"]
        assert len(db.list_tracks()) == 1
        assert db.get_track_by_path("/x/a.flac") is None
        assert db.get_track_by_path("/x/b.flac") is not None
    with TrackDB(pfad, profile_id="schranz") as db:
        assert [t.path for t in db.list_tracks()] == ["/x/a.flac"]
        assert db.get_track_by_path("/x/b.flac") is None


def test_kaufliste_und_gates_werden_getrennt(tmp_path):
    pfad = tmp_path / "t.sqlite"
    with TrackDB(pfad, profile_id="schranz") as db:
        db.upsert_store_item(4711, "T", "A", "https://example.test/x")
        db.upsert_gate_item(4711, "T", "A", "https://example.test/g")
        assert len(db.get_store_items()) == 1
        assert len(db.get_gate_items()) == 1
    with TrackDB(pfad, profile_id="house") as db:
        assert db.get_store_items() == []
        assert db.get_gate_items() == []
def test_rejected_tracks_erscheinen_nicht_in_smartlists(tmp_path):
    from sc_digger.smartlists import Rules, select_tracks

    with TrackDB(tmp_path / "t.sqlite") as db:
        db.upsert_track(TrackRecord(path="/x/a.flac", mtime=1.0, size=2, bpm=155.0, status="archive"))
        db.upsert_track(TrackRecord(path="/x/b.flac", mtime=1.0, size=2, bpm=155.0, status="rejected"))
        treffer = select_tracks(db, Rules())
        assert [t.path for t in treffer] == ["/x/a.flac"]


def test_smartlists_gehoeren_zum_profil(tmp_path):
    pfad = tmp_path / "t.sqlite"
    with TrackDB(pfad, profile_id="schranz") as db:
        db.save_smartlist("peak", {"bpm_min": 150})
        assert db.get_smartlist("peak") is not None
    with TrackDB(pfad, profile_id="house") as db:
        assert db.get_smartlist("peak") is None
        assert [name for name, _ in db.list_smartlists()] == []
        db.save_smartlist("peak", {"bpm_min": 120})   # gleicher Name, anderes Profil
        assert db.get_smartlist("peak")["bpm_min"] == 120
    with TrackDB(pfad, profile_id="schranz") as db:
        assert db.get_smartlist("peak")["bpm_min"] == 150


def test_smartlists_migration_traegt_das_standardprofil(tmp_path):
    from sc_digger.db import MIGRATIONS as _M

    treffer = [m for m in _M if m[1].endswith("_smartlists_profile")]
    assert len(treffer) == 1, "genau eine Migration *_smartlists_profile erwartet"
    version, name, _ = treffer[0]
    assert version >= 23
    assert name.startswith(f"{version:04d}_")
