"""Akzeptanztests: Verlauf „Zuletzt geändert“, Teil 1 (Speicher und erste Schreibstellen)."""
import sqlite3

from sc_digger.db import (
    ACTIVITY_KEEP_DAYS,
    ACTIVITY_SOURCES,
    MIGRATIONS,
    ActivityEntry,
    TrackDB,
    check_migrations,
    log_activity,
)
from sc_digger.web.database import save_match_override, save_track_feedback


def test_migration_und_konstanten():
    assert (24, "0024_activity_log") in {(v, n) for v, n, _ in MIGRATIONS}
    assert check_migrations(MIGRATIONS) == []
    assert ACTIVITY_SOURCES == ("web", "bot", "lauf")
    assert ACTIVITY_KEEP_DAYS == 90


def test_log_und_liste(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        assert log_activity(db.db, "web", "feedback", sc_id=7, detail="like") is True
        assert log_activity(db.db, "bot", "match_override", sc_id=8, detail="not_duplicate") is True
        db.db.commit()
        eintraege = db.list_activity()
    assert [(e.source, e.action, e.sc_id, e.detail) for e in eintraege] == [
        ("bot", "match_override", 8, "not_duplicate"),
        ("web", "feedback", 7, "like"),
    ]
    assert all(isinstance(e, ActivityEntry) and len(e.at) == 19 for e in eintraege)


def test_ungueltiges_wird_nicht_geschrieben(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        assert log_activity(db.db, "mail", "feedback") is False
        assert log_activity(db.db, "web", "") is False
        assert log_activity(db.db, "web", "feedback", sc_id="abc") is False
        db.db.commit()
        assert db.list_activity() == []
    roh = sqlite3.connect(tmp_path / "ohne.sqlite")
    assert log_activity(roh, "web", "feedback", sc_id=1) is False
    roh.close()


def test_alte_eintraege_werden_entfernt(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.db.execute("INSERT INTO activity_log (at, source, action) VALUES ('2000-01-01 00:00:00', 'web', 'alt')")
        db.db.commit()
        assert log_activity(db.db, "web", "neu") is True
        db.db.commit()
        assert [e.action for e in db.list_activity()] == ["neu"]


def test_limit(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        for i in range(5):
            log_activity(db.db, "lauf", f"schritt{i}")
        db.db.commit()
        assert len(db.list_activity(limit=2)) == 2
        assert len(db.list_activity(limit=0)) == 1


def test_bewertung_und_matcher_korrektur_landen_im_verlauf(tmp_path):
    pfad = tmp_path / "tracks.sqlite"
    TrackDB(pfad).close()
    assert save_track_feedback(pfad, 42, "like") is True
    assert save_match_override(pfad, 43, "already_in_collection") is True
    assert save_match_override(pfad, 43, "clear") is True
    with TrackDB(pfad) as db:
        eintraege = [(e.source, e.action, e.sc_id, e.detail) for e in db.list_activity()]
    assert eintraege == [
        ("web", "match_override", 43, "clear"),
        ("web", "match_override", 43, "already_in_collection"),
        ("web", "feedback", 42, "like"),
    ]


def test_speichern_klappt_auch_ohne_verlaufstabelle(tmp_path):
    pfad = tmp_path / "alt.sqlite"
    with sqlite3.connect(pfad) as db:
        db.execute("CREATE TABLE sc_feedback (sc_id INTEGER PRIMARY KEY, url TEXT, value TEXT NOT NULL, "
                   "updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    assert save_track_feedback(pfad, 5, "later") is True
    with sqlite3.connect(pfad) as db:
        assert db.execute("SELECT value FROM sc_feedback WHERE sc_id = 5").fetchone() == ("later",)
