"""Eigene Tests für den Verlauf „Zuletzt geändert“ (Issue #619, Teil 1 von #596)."""
import sqlite3

from sc_digger.db import TrackDB, log_activity


def test_log_activity_committet_nicht_selbst(tmp_path):
    pfad = tmp_path / "tracks.sqlite"
    with TrackDB(pfad) as db:
        assert log_activity(db.db, "web", "feedback", sc_id=1)
        db.db.rollback()
        assert db.list_activity() == []


def test_sc_id_darf_fehlen(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        assert log_activity(db.db, "lauf", "rekordbox_sync", detail="12 Tracks")
        db.db.commit()
        eintrag = db.list_activity()[0]
    assert eintrag.sc_id is None and eintrag.detail == "12 Tracks"


def test_ungueltiger_feedback_wert_schreibt_keinen_verlauf(tmp_path):
    from sc_digger.web.database import save_track_feedback
    pfad = tmp_path / "tracks.sqlite"
    TrackDB(pfad).close()
    assert save_track_feedback(pfad, 9, "toll") is False
    with TrackDB(pfad) as db:
        assert db.list_activity() == []


def test_fehlende_datei_wird_nicht_angelegt(tmp_path):
    from sc_digger.web.database import save_match_override
    pfad = tmp_path / "fehlt.sqlite"
    assert save_match_override(pfad, 3, "not_duplicate") is False
    assert not pfad.exists()


def test_lesen_ohne_verbindungsfehler_nach_migration(tmp_path):
    pfad = tmp_path / "tracks.sqlite"
    TrackDB(pfad).close()
    with sqlite3.connect(pfad) as db:
        assert db.execute("SELECT COUNT(*) FROM activity_log").fetchone() == (0,)
