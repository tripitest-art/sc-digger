"""query_digest und die Abhak-Marke von Gates (Teil 3 von #591, Issue #648).

Der Akzeptanztest ersetzt query_digest durch ein Fake; diese Tests laufen deshalb gegen
echte temporäre Datenbanken, damit die SQL-Logik für gate_done_at wirklich geprüft wird."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from sc_digger.db import TrackDB
from sc_digger.web.database import query_digest

GATE_ERLEDIGT, GATE_OFFEN, OHNE_GATE = 801, 802, 803


def _dbs(tmp_path: Path) -> tuple[Path, Path]:
    state_db = tmp_path / "seen.sqlite"
    track_db = tmp_path / "tracks.sqlite"
    with TrackDB(track_db) as db:
        db.upsert_gate_item(GATE_ERLEDIGT, "Erledigt", "DJ A", "https://hypeddit.com/801", "hypeddit")
        db.upsert_gate_item(GATE_OFFEN, "Offen", "DJ B", "https://hypeddit.com/802", "hypeddit")
        assert db.mark_gate_item_done(GATE_ERLEDIGT, True)
    with sqlite3.connect(state_db) as sdb:
        sdb.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        sdb.executemany("INSERT INTO seen VALUES (?, ?, ?, ?)", [
            (GATE_ERLEDIGT, "https://soundcloud.com/a/erledigt", "DJ A - Erledigt", "2026-11-19 10:00:00"),
            (GATE_OFFEN, "https://soundcloud.com/b/offen", "DJ B - Offen", "2026-11-19 11:00:00"),
            (OHNE_GATE, "https://soundcloud.com/c/stream", "DJ C - Stream", "2026-11-19 12:00:00"),
        ])
    return state_db, track_db


def _by_id(state_db: Path, track_db: Path) -> dict:
    return {i.sc_id: i for i in query_digest(state_db, track_db).items}


def test_gate_mit_done_at_traegt_die_marke(tmp_path):
    items = _by_id(*_dbs(tmp_path))
    item = items[GATE_ERLEDIGT]
    assert item.gate_done_at  # Zeitstempel aus gate_items.done_at
    assert item.download_kind == "gate"
    assert item.gate_url == "https://hypeddit.com/801"


def test_gate_ohne_done_at_ist_offen(tmp_path):
    item = _by_id(*_dbs(tmp_path))[GATE_OFFEN]
    assert item.gate_done_at is None
    assert item.download_kind == "gate"
    assert item.gate_url == "https://hypeddit.com/802"


def test_track_ohne_gate_hat_keine_marke(tmp_path):
    item = _by_id(*_dbs(tmp_path))[OHNE_GATE]
    assert item.gate_done_at is None
    assert item.download_kind == "stream"
    assert item.gate_url is None
    assert item.to_dict()["gate_done_at"] is None


def test_wieder_oeffnen_nimmt_die_marke_zurueck(tmp_path):
    state_db, track_db = _dbs(tmp_path)
    with TrackDB(track_db) as db:
        assert db.mark_gate_item_done(GATE_ERLEDIGT, False)
    assert _by_id(state_db, track_db)[GATE_ERLEDIGT].gate_done_at is None


def test_alte_db_ohne_done_at_spalte_wirft_nicht(tmp_path):
    """Vor Migration 25 fehlt die Spalte: der Digest soll Gates dann als offen zeigen."""
    state_db, track_db = _dbs(tmp_path)
    with sqlite3.connect(track_db) as tdb:
        tdb.execute("ALTER TABLE gate_items DROP COLUMN done_at")
    item = _by_id(state_db, track_db)[GATE_ERLEDIGT]
    assert item.gate_done_at is None
    assert item.download_kind == "gate"
    assert item.gate_url == "https://hypeddit.com/801"
