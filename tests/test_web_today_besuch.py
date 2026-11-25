"""Eigene Tests zu Issue #710: Besuchsstand und "neu seit"-Zählung der Heute-Karte."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from sc_digger.web.database import query_digest
from sc_digger.web.today import next_visit_state


def test_cookie_mit_kaputtem_zeitstempel_zaehlt_als_kein_cookie():
    jetzt = datetime(2026, 11, 25, 8, 0, 0)
    assert next_visit_state("x|y", jetzt) == (None, "|2026-11-25 08:00:00")
    assert next_visit_state("a|b|c", jetzt) == (None, "|2026-11-25 08:00:00")
    assert next_visit_state("", jetzt) == (None, "|2026-11-25 08:00:00")


def test_genau_die_pause_beginnt_einen_neuen_besuch():
    # Grenze: >= VISIT_GAP_S (1800 s)
    assert next_visit_state("|2026-11-25 08:00:00", datetime(2026, 11, 25, 8, 30, 0)) == (
        "2026-11-25 08:00:00", "2026-11-25 08:00:00|2026-11-25 08:30:00")


def test_query_digest_since_filtert_nach_meldezeit(tmp_path: Path):
    db = tmp_path / "seen.sqlite"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        con.execute("INSERT INTO seen VALUES (1, 'u1', 'A - T1', '2026-11-20 07:00:00')")
        con.execute("INSERT INTO seen VALUES (2, 'u2', 'B - T2', '2026-11-25 07:00:00')")
    tracks = tmp_path / "tracks.sqlite"
    alle = query_digest(db, tracks, limit=10)
    neu = query_digest(db, tracks, limit=10, since="2026-11-24 00:00:00")
    assert alle.total == 2
    assert neu.total == 1 and neu.items[0].reported_at == "2026-11-25 07:00:00"
    assert query_digest(db, tracks, limit=10, since="2026-11-25 07:00:00").total == 0
