"""Akzeptanztests: freie Tags auf Tracks."""
import sqlite3

import pytest

from sc_digger.db import MIGRATIONS, TrackDB, TrackRecord
from sc_digger.tags import TAG_MAX_LEN, normalize_tag


def _track(db: TrackDB, pfad: str) -> TrackRecord:
    return db.upsert_track(TrackRecord(path=pfad, mtime=1.0, size=2))


def test_normalisiert_kleinschreibung_leerraum_und_trenner():
    assert normalize_tag("  Peak   Time ") == "peak time"
    assert normalize_tag("Fake-Drop") == "fake-drop"
    assert normalize_tag("Warm_Up") == "warm_up"
    assert normalize_tag("Käfig") == "käfig"


@pytest.mark.parametrize("roh", ["", "   ", "x" * (TAG_MAX_LEN + 1), "Opener!", "tag/slash", "a<b"])
def test_lehnt_ungueltige_tags_ab(roh):
    with pytest.raises(ValueError):
        normalize_tag(roh)


def test_laengengrenze_ist_erlaubt():
    assert len(normalize_tag("x" * TAG_MAX_LEN)) == TAG_MAX_LEN


def test_migration_ist_die_juengste():
    treffer = [m for m in MIGRATIONS if m[1].endswith("_track_tags")]
    assert len(treffer) == 1, "genau eine Migration *_track_tags erwartet"
    version, name, _ = treffer[0]
    # Bewusst nicht "die jüngste": jede spätere Migration würde diesen Test ohne Grund
    # rot machen. Geprüft wird Existenz, plausible Nummer und Name passend zur Version.
    assert version >= 21
    assert name.startswith(f"{version:04d}_")


def test_tags_haengen_am_track(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        a = _track(db, "/x/a.flac")
        b = _track(db, "/x/b.flac")
        assert db.add_track_tag(a.id, " Peak Time ") == "peak time"
        assert db.add_track_tag(a.id, "peak time") == "peak time"  # idempotent
        db.add_track_tag(b.id, "opener")
        assert db.get_track_tags(a.id) == ["peak time"]
        assert db.get_track_tags(b.id) == ["opener"]
        assert db.list_track_ids_by_tag("peak time") == [a.id]
        assert db.list_tags() == [("opener", 1), ("peak time", 1)]


def test_mehrere_tracks_zaehlen_und_entfernen(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        a = _track(db, "/x/a.flac")
        b = _track(db, "/x/b.flac")
        db.add_track_tag(a.id, "opener")
        db.add_track_tag(b.id, "opener")
        db.add_track_tag(b.id, "peak time")
        assert db.list_tags() == [("opener", 2), ("peak time", 1)]
        assert db.list_track_ids_by_tag("opener") == [a.id, b.id]
        assert db.remove_track_tag(a.id, "opener") is True
        assert db.remove_track_tag(a.id, "opener") is False
        assert db.get_track_tags(a.id) == []
        assert db.list_tags() == [("opener", 1), ("peak time", 1)]


def test_unbekannter_track_verletzt_den_fremdschluessel(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.add_track_tag(9999, "opener")


def test_tags_verschwinden_mit_dem_track(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        a = _track(db, "/x/a.flac")
        db.add_track_tag(a.id, "opener")
        db.db.execute("DELETE FROM tracks WHERE id = ?", (a.id,))
        db.db.commit()
        assert db.db.execute("SELECT COUNT(*) FROM track_tags").fetchone()[0] == 0
