"""Akzeptanztests: Kuratoren-Übersicht aus der Sammlung."""
from sc_digger.curator import curator_overview
from sc_digger.db import TrackDB, TrackRecord


def _track(db, pfad, artist, *, rating=None, bpm=None, status="archive"):
    return db.upsert_track(TrackRecord(path=pfad, mtime=1.0, size=2,
                                       artist=artist, rb_rating=rating, bpm=bpm, status=status))


def test_gruppiert_sortiert_und_rechnet(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        _track(db, "/x/a1.flac", "LabelA", rating=5, bpm=150.0)
        _track(db, "/x/a2.flac", "LabelA", rating=1, bpm=160.0)
        _track(db, "/x/a3.flac", "LabelA", rating=4, bpm=None)
        _track(db, "/x/b1.flac", "LabelB", rating=5, bpm=140.0)
        _track(db, "/x/b2.flac", "LabelB", rating=2, bpm=142.0)
        _track(db, "/x/c1.flac", "LabelC", rating=5, bpm=130.0)

        eintraege = curator_overview(db, min_tracks=2)
        assert [e.artist for e in eintraege] == ["LabelA", "LabelB"]
        a = eintraege[0]
        assert a.track_count == 3
        assert abs(a.rated_share - 2 / 3) < 1e-9
        assert abs(a.avg_bpm - 155.0) < 1e-9
        assert [t.path.split("/")[-1] for t in a.tracks] == ["a1.flac", "a2.flac", "a3.flac"]


def test_limit_und_leere_kuenstler(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        for i in range(4):
            _track(db, f"/x/n{i}.flac", f"Label{i}", rating=5)
        _track(db, "/x/o1.flac", "", rating=5)
        _track(db, "/x/o2.flac", "", rating=5)
        assert curator_overview(db, min_tracks=1, limit=2) != []
        assert len(curator_overview(db, min_tracks=1, limit=2)) == 2
        namen = [e.artist for e in curator_overview(db, min_tracks=1)]
        assert "" not in namen
        assert namen == ["Label0", "Label1", "Label2", "Label3"]


def test_inbox_tracks_zaehlen_nicht_und_ohne_bpm_kein_durchschnitt(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        _track(db, "/x/i1.flac", "LabelD", rating=5, bpm=None, status="inbox")
        _track(db, "/x/d1.flac", "LabelD", rating=None, bpm=None)
        eintraege = curator_overview(db, min_tracks=1)
        assert [e.track_count for e in eintraege] == [1]
        assert eintraege[0].avg_bpm is None
        assert eintraege[0].rated_share == 0.0


def test_rueckgabe_ist_stabil_bei_gleichstand(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        _track(db, "/x/z1.flac", "Zeta", rating=5)
        _track(db, "/x/y1.flac", "Alpha", rating=5)
        assert [e.artist for e in curator_overview(db, min_tracks=1)] == ["Alpha", "Zeta"]
