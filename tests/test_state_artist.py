"""Unit-Tests für die Speicherung des Künstlers in seen und rejected (#435)."""
import sqlite3
from pathlib import Path

import pytest

from sc_digger.models import Track
from sc_digger.output import State


def _make_track(track_id: int, title: str, artist: str = "") -> Track:
    return Track(
        id=track_id, title=title, url=f"https://soundcloud.com/test/{track_id}",
        artist=artist, artist_url="", created_at="2026-10-31T00:00:00Z", duration_ms=180000,
        genre="Schranz", tags=[], description="", bpm=150.0, plays=0, likes=0,
        reposts=0, comments=0, downloadable=True, has_downloads_left=True,
        purchase_url=None, purchase_title=None,
    )


def test_state_artist_leerer_kuenstler_wird_null_in_seen_und_rejected(tmp_path: Path):
    db = tmp_path / "seen.sqlite"
    with State(db) as st:
        st.mark_one(_make_track(1, "Track Ohne Artist", ""))
        st.mark_one(_make_track(2, "Track Nur Whitespace", "   "))
        st.mark_rejected(_make_track(3, "Rejected Ohne Artist", ""), reason="Fake")
        st.mark_rejected(_make_track(4, "Rejected Whitespace", " \t "), reason="Clipping")

    with sqlite3.connect(db) as conn:
        for sid in (1, 2):
            row = conn.execute("SELECT artist FROM seen WHERE id = ?", (sid,)).fetchone()
            assert row[0] is None, f"seen {sid} artist sollte NULL sein"
        for sid in (3, 4):
            row = conn.execute("SELECT artist FROM rejected WHERE id = ?", (sid,)).fetchone()
            assert row[0] is None, f"rejected {sid} artist sollte NULL sein"


def test_state_mark_mehrere_tracks_batch(tmp_path: Path):
    db = tmp_path / "seen.sqlite"
    tracks = [
        _make_track(10, "Title 1", "Artist One"),
        _make_track(11, "Title 2", "Artist Two"),
        _make_track(12, "Title 3", ""),
    ]
    with State(db) as st:
        st.mark(tracks)

    with sqlite3.connect(db) as conn:
        rows = dict(conn.execute("SELECT id, artist FROM seen").fetchall())
        assert rows[10] == "Artist One"
        assert rows[11] == "Artist Two"
        assert rows[12] is None


def test_zweite_state_oeffnung_ist_idempotent(tmp_path: Path):
    db = tmp_path / "seen.sqlite"
    with State(db) as st1:
        st1.mark_one(_make_track(1, "T1", "A1"))

    # Zweite Öffnung darf keine Fehler werfen und die Spalten nicht doppelt anlegen
    with State(db) as st2:
        st2.mark_one(_make_track(2, "T2", "A2"))

    with sqlite3.connect(db) as conn:
        cols_seen = [r[1] for r in conn.execute("PRAGMA table_info(seen)")]
        cols_rejected = [r[1] for r in conn.execute("PRAGMA table_info(rejected)")]
        assert cols_seen.count("artist") == 1
        assert cols_rejected.count("artist") == 1


def test_insert_or_ignore_ueberschreibt_bestehenden_kuenstler_nicht(tmp_path: Path):
    db = tmp_path / "seen.sqlite"
    with State(db) as st:
        st.mark_one(_make_track(99, "Original Title", "Original Artist"))
        # Erneuter Aufruf mit geänderten Daten: muss ignoriert werden
        st.mark_one(_make_track(99, "New Title", "New Artist"))
        st.mark([_make_track(99, "Third Title", "Third Artist")])

    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT title, artist FROM seen WHERE id = 99").fetchone()
        assert row[0] == "Original Title"
        assert row[1] == "Original Artist"
