"""Eigene Tests für den Track-Lebenslauf mit Handkorrekturen (Issue #692)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.web.track_life import OVERRIDE_LABELS, _collect, collect_track_life

ROOT = Path(__file__).resolve().parents[1]


def _cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def test_override_labels_mapping():
    assert OVERRIDE_LABELS == {
        "already_in_collection": "bereits in Sammlung",
        "not_duplicate": "kein Duplikat",
    }


def test_ohne_korrektur_ist_override_none(tmp_path):
    cfg = _cfg(tmp_path)
    with TrackDB(tmp_path / "tracks.sqlite") as tdb:
        tdb.upsert_track(TrackRecord(
            path="/inbox/alpha.mp3", mtime=1.0, size=1000, artist="Alpha",
            title="Erster Track", bpm=155.0, key_camelot="8A", status="inbox", sc_id=101,
        ))
    life = _collect(cfg, 101)
    assert life is not None
    assert life.override is None
    assert "override" not in [s.key for s in life.steps]
    assert "Handkorrektur" not in [s.label for s in life.steps]


def test_mit_korrektur_already_in_collection(tmp_path):
    cfg = _cfg(tmp_path)
    with TrackDB(tmp_path / "tracks.sqlite") as tdb:
        tdb.upsert_track(TrackRecord(
            path="/inbox/alpha.mp3", mtime=1.0, size=1000, artist="Alpha",
            title="Erster Track", bpm=155.0, key_camelot="8A", status="inbox", sc_id=102,
        ))
        tdb.set_match_override(102, "already_in_collection", "Dashboard")
    life = _collect(cfg, 102)
    assert life is not None
    assert life.override == "already_in_collection"
    override_steps = [s for s in life.steps if s.key == "override"]
    assert len(override_steps) == 1
    step = override_steps[0]
    assert step.label == "Handkorrektur"
    assert step.detail == "bereits in Sammlung"
    assert step.at is not None
    assert life.current == "override"


def test_mit_korrektur_not_duplicate(tmp_path):
    cfg = _cfg(tmp_path)
    with TrackDB(tmp_path / "tracks.sqlite") as tdb:
        tdb.upsert_track(TrackRecord(
            path="/inbox/beta.mp3", mtime=1.0, size=1000, artist="Beta",
            title="Zweiter Track", bpm=150.0, key_camelot="9A", status="inbox", sc_id=103,
        ))
        tdb.set_match_override(103, "not_duplicate", "Telegram")
    life = _collect(cfg, 103)
    assert life is not None
    assert life.override == "not_duplicate"
    override_steps = [s for s in life.steps if s.key == "override"]
    assert len(override_steps) == 1
    assert override_steps[0].detail == "kein Duplikat"


def test_unbekannter_override_type_unveraendert_durchgereicht(tmp_path):
    cfg = _cfg(tmp_path)
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        db.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT, mtime REAL, size INTEGER, "
            "sc_id INTEGER, artist TEXT, title TEXT, bpm REAL, key_camelot TEXT, status TEXT)"
        )
        db.execute(
            "INSERT INTO tracks (id, path, mtime, size, sc_id, artist, title, bpm, key_camelot, status) "
            "VALUES (1, '/inbox/gamma.mp3', 1.0, 1000, 104, 'Gamma', 'Dritter Track', 145.0, '10A', 'inbox')"
        )
        db.execute(
            "CREATE TABLE match_overrides (sc_id INTEGER PRIMARY KEY, override_type TEXT, note TEXT, updated_at TEXT)"
        )
        db.execute(
            "INSERT INTO match_overrides (sc_id, override_type, note, updated_at) "
            "VALUES (104, 'custom_override_val', 'Manuell', '2026-11-22 12:00:00')"
        )
    life = _collect(cfg, 104)
    assert life is not None
    assert life.override == "custom_override_val"
    override_steps = [s for s in life.steps if s.key == "override"]
    assert len(override_steps) == 1
    assert override_steps[0].detail == "custom_override_val"
    assert override_steps[0].at == "2026-11-22 12:00:00"


def test_tabelle_ohne_updated_at_spalte_liefert_none_als_zeit(tmp_path):
    cfg = _cfg(tmp_path)
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        db.execute("CREATE TABLE match_overrides (sc_id INTEGER PRIMARY KEY, override_type TEXT)")
        db.execute("INSERT INTO match_overrides (sc_id, override_type) VALUES (105, 'already_in_collection')")
    life = collect_track_life(cfg, 105)
    assert life is not None
    assert life.override == "already_in_collection"
    override_steps = [s for s in life.steps if s.key == "override"]
    assert len(override_steps) == 1
    assert override_steps[0].at is None
    assert override_steps[0].detail == "bereits in Sammlung"
