"""Eigene Tests für den Track-Lebenslauf (Issue #615): Randfälle von collect_track_life."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.output import State
from sc_digger.web.routes.track_detail import safe_link
from sc_digger.web.track_life import FEEDBACK_LABELS, collect_track_life

ROOT = Path(__file__).resolve().parents[1]


def _cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def test_feedback_labels():
    assert FEEDBACK_LABELS == {"like": "gefällt", "dislike": "passt nicht", "later": "später"}


def test_safe_link():
    assert safe_link("https://hypeddit.com/x") == "https://hypeddit.com/x"
    assert safe_link("http://example.org") == "http://example.org"
    assert safe_link("javascript:alert(1)") is None
    assert safe_link("  https://x") is None
    assert safe_link("/lokal/pfad.wav") is None
    assert safe_link(None) is None


def test_store_und_spaeter_ohne_seen(tmp_path):
    cfg = _cfg(tmp_path)
    with TrackDB(tmp_path / "tracks.sqlite"):
        pass
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        db.execute("INSERT INTO sc_feedback (sc_id, url, value, updated_at) VALUES (21, 'u', 'later', '2026-11-02')")
        db.execute("INSERT INTO store_items (profile_id, sc_id, title, artist, purchase_url, first_seen) VALUES "
                   "('schranz', 21, 'Kauf', 'Artist S', 'https://bandcamp.com/k', '2026-11-03 10:00:00')")
        # Zweites Profil, später gefunden: der frühere Fund zählt.
        db.execute("INSERT INTO store_items (profile_id, sc_id, title, artist, purchase_url, first_seen) VALUES "
                   "('hardtechno', 21, 'Kauf', 'Artist S', 'https://beatport.com/k', '2026-11-09 10:00:00')")
    life = collect_track_life(cfg, 21)
    assert [s.key for s in life.steps] == ["rated", "store"]
    assert life.steps[0].detail == "später"
    assert life.steps[1].detail == "https://bandcamp.com/k"
    assert life.steps[1].at == "2026-11-03 10:00:00"
    # Ohne seen/rejected kommen Artist und Titel aus store_items, eine URL gibt es nicht.
    assert (life.artist, life.title, life.url) == ("Artist S", "Kauf", None)
    assert life.current == "store"


def test_archiv_ueber_dateinamen_ohne_sc_id(tmp_path):
    cfg = _cfg(tmp_path)
    with TrackDB(tmp_path / "tracks.sqlite") as tdb:
        tdb.upsert_track(TrackRecord(path="/music/Schranz/[31] X - Alt.wav", mtime=0.0, size=1,
                                     status="archive", artist="X", title="Alt"))
    life = collect_track_life(cfg, 31)
    assert [s.key for s in life.steps] == ["archive"]
    assert life.steps[0].detail == "/music/Schranz/[31] X - Alt.wav"
    # „[31]“ im Dateinamen darf nicht zu ID 3 gehören (Klammern sind Teil des Suchtexts).
    assert collect_track_life(cfg, 3) is None


def test_inbox_zeit_als_utc_text_und_analyse_ohne_key(tmp_path):
    cfg = _cfg(tmp_path)
    with TrackDB(tmp_path / "tracks.sqlite") as tdb:
        tdb.upsert_track(TrackRecord(path="/inbox/[41] Y - Neu.wav", mtime=86400.0, size=1,
                                     sc_id=41, status="inbox", bpm=152.5))
    life = collect_track_life(cfg, 41)
    # Ohne Camelot-Key gilt der Track noch nicht als analysiert.
    assert [s.key for s in life.steps] == ["inbox"]
    assert life.steps[0].at == "1970-01-02 00:00:00"
    # Ohne Titel in irgendeiner Quelle greift der Ersatztitel.
    assert life.title == "Track 41"


def test_uebernommene_zeile_geht_vor_archivzeile(tmp_path):
    cfg = _cfg(tmp_path)
    with TrackDB(tmp_path / "tracks.sqlite") as tdb:
        tdb.upsert_track(TrackRecord(path="/music/Schranz/Z - Fertig.wav", mtime=0.0, size=1,
                                     sc_id=51, status="archive", title="Fertig"))
        tdb.upsert_track(TrackRecord(path="/inbox/[51] Z - Fertig.wav", mtime=0.0, size=1,
                                     sc_id=51, status="inbox", bpm=150.0, key_camelot="5A",
                                     adopted_at="2026-11-10 12:00:00"))
    life = collect_track_life(cfg, 51)
    assert [s.key for s in life.steps] == ["inbox", "analyzed", "adopted"]
    assert life.steps[1].detail == "150 BPM · 5A"


def test_fehlende_tabellen_und_spalten(tmp_path):
    cfg = _cfg(tmp_path)
    # State-DB nur mit einer seen-Tabelle ohne reported_at, Track-DB ganz ohne Tabellen.
    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        db.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, title TEXT)")
        db.execute("INSERT INTO seen (id, title) VALUES (61, 'Nur Titel')")
    sqlite3.connect(tmp_path / "tracks.sqlite").close()
    life = collect_track_life(cfg, 61)
    assert [(s.key, s.at) for s in life.steps] == [("found", None)]
    assert (life.artist, life.title, life.url) == (None, "Nur Titel", None)


def test_liest_nur_und_wirft_nie(tmp_path):
    cfg = _cfg(tmp_path)
    State(tmp_path / "seen.sqlite").db.close()
    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        db.execute("INSERT INTO seen (id, url, title) VALUES (71, 'https://soundcloud.com/x', 'A - B')")
    before = (tmp_path / "seen.sqlite").read_bytes()
    assert collect_track_life(cfg, 71).current == "found"
    assert (tmp_path / "seen.sqlite").read_bytes() == before
    assert not (tmp_path / "tracks.sqlite").exists()
    # Kaputte Datei statt SQLite, bool und None als ID: kein Fehler nach außen.
    (tmp_path / "tracks.sqlite").write_text("kein sqlite", encoding="utf-8")
    assert collect_track_life(cfg, 71).current == "found"
    assert collect_track_life(cfg, True) is None
    assert collect_track_life(cfg, None) is None
    broken = Config.load(ROOT / "config.yaml")
    broken.raw["state"] = None
    assert collect_track_life(broken, 71) is None


def test_track_id_aus_zeile_ueber_sc_id(tmp_path):
    cfg = _cfg(tmp_path)
    with TrackDB(tmp_path / "tracks.sqlite") as tdb:
        tdb.upsert_track(TrackRecord(path="/inbox/A - T.wav", mtime=0.0, size=1,
                                     sc_id=61, status="inbox", title="T"))
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        erwartet = db.execute("SELECT id FROM tracks WHERE sc_id = 61").fetchone()[0]
    life = collect_track_life(cfg, 61)
    assert life.track_id == erwartet


def test_track_id_aus_zeile_ueber_dateinamen(tmp_path):
    cfg = _cfg(tmp_path)
    with TrackDB(tmp_path / "tracks.sqlite") as tdb:
        tdb.upsert_track(TrackRecord(path="/music/Schranz/[62] X - Alt.wav", mtime=0.0, size=1,
                                     status="archive", title="Alt"))
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        erwartet = db.execute("SELECT id FROM tracks WHERE path LIKE '%[62]%'").fetchone()[0]
    assert collect_track_life(cfg, 62).track_id == erwartet


def test_track_id_ohne_zeile_ist_none(tmp_path):
    cfg = _cfg(tmp_path)
    with TrackDB(tmp_path / "tracks.sqlite") as tdb:
        pass
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        db.execute("INSERT INTO sc_feedback (sc_id, url, value, updated_at) VALUES (63, 'u', 'like', '2026-11-02')")
    life = collect_track_life(cfg, 63)
    assert life is not None and life.track_id is None
