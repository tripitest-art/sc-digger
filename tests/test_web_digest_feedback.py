"""Tests für die Digest-Feedback-Knöpfe und Barrierefreiheit (Issue #576)."""
from pathlib import Path
import re
import sqlite3
import pytest
from starlette.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app


def _make_app(tmp_path: Path):
    state_db = tmp_path / "seen.sqlite"
    track_db = tmp_path / "tracks.sqlite"

    with sqlite3.connect(state_db) as db:
        db.execute(
            "CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)"
        )
        db.execute(
            "INSERT INTO seen VALUES (101, 'https://soundcloud.com/artist/track-101', 'Artist - Track 101', '2026-10-14 12:00:00')"
        )
        db.execute(
            "INSERT INTO seen VALUES (102, 'https://soundcloud.com/artist/track-102', 'Artist - Track 102', '2026-10-14 12:05:00')"
        )

    with sqlite3.connect(track_db) as db:
        db.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT, status TEXT)")
        db.execute("CREATE TABLE sc_feedback (sc_id INTEGER PRIMARY KEY, url TEXT, value TEXT, updated_at TEXT)")
        # 101 hat like-Feedback, 102 hat kein Feedback
        db.execute("INSERT INTO sc_feedback VALUES (101, NULL, 'like', '2026-10-14 12:01:00')")

    cfg = Config({
        "state": {
            "db_path": str(state_db),
            "track_db_path": str(track_db),
        }
    })
    return create_app(cfg, password=None, allow_anonymous=True)


def test_digest_feedback_buttons_aria_pressed_and_icons(tmp_path: Path):
    app = _make_app(tmp_path)
    client = TestClient(app)

    res = client.get("/digest")
    assert res.status_code == 200
    html = res.text

    # Die Feedback-Knöpfe nutzen SVG-Icons statt Emojis
    assert "icon-like" in html
    assert "icon-dislike" in html
    assert "icon-later" in html

    # aria-pressed vorhanden
    assert 'aria-pressed="true"' in html
    assert 'aria-pressed="false"' in html

    # data-feedback-cell für 101 enthält aktive Like-Schaltfläche mit aria-pressed="true"
    assert 'data-sc-id="101"' in html
    assert 'data-value="like" title="Gefällt mir"' in html

    # Regex: Track 101 Like-Knopf ist active und aria-pressed="true"
    m_101_like = re.search(r'data-value="like"[^>]*aria-pressed="true"', html)
    assert m_101_like is not None

    # Track 101 Dislike- und Later-Knöpfe sind aria-pressed="false"
    m_101_dislike = re.search(r'data-value="dislike"[^>]*aria-pressed="false"', html)
    assert m_101_dislike is not None
