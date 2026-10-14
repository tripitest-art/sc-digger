"""Eigene Tests zu Issue #257: Engagement-Kennzahlen in URL-Scanner und Digest-Historie.

Ergänzen die Akzeptanztests um die Kanten, die sie nicht abdecken:
* ``query_digest`` nimmt bei mehreren Snapshots den jüngsten und liefert ohne
  ``track_snapshots``-Tabelle die Standardwerte 0 (keine Exception).
* ``_scan_url`` liefert neben plays/likes/reposts auch comments und like_ratio.
* ``/digest`` rendert die kompakte Meta-Zeile unter dem Titel und lässt sie weg, wenn
  keine Kennzahlen vorliegen.

Kein Netz: ``SoundCloudClient`` wird im Modul ``sc_digger.web.app`` durch einen Fake ersetzt.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import sc_digger.web.app as app_module
from sc_digger.models import Config, Track
from sc_digger.web.app import _scan_url, create_app
from sc_digger.web.database import query_digest


def _seed_seen(tmp_path: Path, rows: list[tuple[int, str, str, str]]) -> Path:
    state_db = tmp_path / "seen.sqlite"
    with sqlite3.connect(state_db) as db:
        db.execute(
            "CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)"
        )
        db.executemany("INSERT INTO seen VALUES (?, ?, ?, ?)", rows)
    return state_db


def _cfg(tmp_path: Path) -> Config:
    return Config({
        "state": {
            "db_path": str(tmp_path / "seen.sqlite"),
            "track_db_path": str(tmp_path / "tracks.sqlite"),
        }
    })


def test_query_digest_uses_latest_snapshot(tmp_path: Path):
    """Bei mehreren Snapshots je Track zählt der jüngste (recorded_at, dann id)."""
    state_db = _seed_seen(
        tmp_path, [(123, "https://soundcloud.com/a/t", "Artist - Track", "2026-10-14 12:00:00")]
    )
    track_db = tmp_path / "tracks.sqlite"
    with sqlite3.connect(track_db) as db:
        db.execute(
            "CREATE TABLE track_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, sc_id INTEGER, "
            "artist TEXT, title TEXT, plays INTEGER, likes INTEGER, reposts INTEGER, "
            "comments INTEGER, recorded_at TEXT)"
        )
        db.execute(
            "INSERT INTO track_snapshots (sc_id, artist, title, plays, likes, reposts, comments, "
            "recorded_at) VALUES (123, 'A', 'T', 100, 1, 2, 3, '2026-10-01 12:00:00')"
        )
        db.execute(
            "INSERT INTO track_snapshots (sc_id, artist, title, plays, likes, reposts, comments, "
            "recorded_at) VALUES (123, 'A', 'T', 4200, 310, 45, 12, '2026-10-14 12:00:00')"
        )

    item = query_digest(state_db, track_db).items[0]
    assert (item.plays, item.likes, item.reposts, item.comments) == (4200, 310, 45, 12)


def test_query_digest_without_snapshot_table_defaults_zero(tmp_path: Path):
    """Fehlt track_snapshots, bleiben die Kennzahlen 0 statt eines Fehlers."""
    state_db = _seed_seen(
        tmp_path, [(123, "https://soundcloud.com/a/t", "Artist - Track", "2026-10-14 12:00:00")]
    )
    track_db = tmp_path / "tracks.sqlite"
    with sqlite3.connect(track_db) as db:
        db.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT, status TEXT)")

    item = query_digest(state_db, track_db).items[0]
    assert (item.plays, item.likes, item.reposts, item.comments) == (0, 0, 0, 0)


def test_scan_url_returns_all_engagement_fields(tmp_path: Path, monkeypatch):
    """Der Scanner gibt neben plays/likes/reposts auch comments und like_ratio zurück."""

    class FakeSC:
        def resolve_kind(self, url):
            return "track"

        def track_station(self, url, limit=50):
            track = Track(
                id=9, title="T", url="https://soundcloud.com/x/t", artist="A",
                artist_url="https://soundcloud.com/x", created_at="2026-10-14T12:00:00Z",
                duration_ms=180000, genre="Schranz", tags=[], description="", bpm=150.0,
                plays=1000, likes=250, reposts=30, comments=8, downloadable=False,
                has_downloads_left=False, purchase_url=None, purchase_title=None,
            )
            return "Station", [track]

    monkeypatch.setattr(app_module, "SoundCloudClient", FakeSC)
    result = _scan_url(_cfg(tmp_path), "https://soundcloud.com/x/t")

    entry = result["tracks"][0]
    assert entry["plays"] == 1000
    assert entry["likes"] == 250
    assert entry["reposts"] == 30
    assert entry["comments"] == 8
    assert entry["like_ratio"] == 0.25


def test_digest_page_shows_compact_engagement_line(tmp_path: Path):
    """Die Digest-Seite zeigt die Kennzahlen kompakt unter dem Titel."""
    _seed_seen(
        tmp_path, [(123, "https://soundcloud.com/a/t", "Artist - Track", "2026-10-14 12:00:00")]
    )
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        db.execute(
            "CREATE TABLE track_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, sc_id INTEGER, "
            "artist TEXT, title TEXT, plays INTEGER, likes INTEGER, reposts INTEGER, "
            "comments INTEGER, recorded_at TEXT)"
        )
        db.execute(
            "INSERT INTO track_snapshots (sc_id, artist, title, plays, likes, reposts, comments, "
            "recorded_at) VALUES (123, 'Artist', 'Track', 4200, 310, 45, 12, '2026-10-14 12:00:00')"
        )

    client = TestClient(create_app(_cfg(tmp_path), password=None, allow_anonymous=True))
    html = client.get("/digest").text
    assert "▶ 4.2k · ♥ 310 · 🔁 45" in html


def test_digest_page_omits_line_without_snapshot(tmp_path: Path):
    """Ohne Snapshot steht unter dem Titel keine (irreführende) Null-Zeile."""
    _seed_seen(
        tmp_path, [(123, "https://soundcloud.com/a/t", "Artist - Track", "2026-10-14 12:00:00")]
    )
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        db.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT, status TEXT)")

    client = TestClient(create_app(_cfg(tmp_path), password=None, allow_anonymous=True))
    html = client.get("/digest").text
    assert "Track" in html
    assert "▶" not in html


def test_api_digest_exposes_engagement_fields(tmp_path: Path):
    """Die JSON-API liefert die Kennzahlen mit, damit auch Skripte sie sehen."""
    _seed_seen(
        tmp_path, [(123, "https://soundcloud.com/a/t", "Artist - Track", "2026-10-14 12:00:00")]
    )
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        db.execute(
            "CREATE TABLE track_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, sc_id INTEGER, "
            "artist TEXT, title TEXT, plays INTEGER, likes INTEGER, reposts INTEGER, "
            "comments INTEGER, recorded_at TEXT)"
        )
        db.execute(
            "INSERT INTO track_snapshots (sc_id, artist, title, plays, likes, reposts, comments, "
            "recorded_at) VALUES (123, 'Artist', 'Track', 4200, 310, 45, 12, '2026-10-14 12:00:00')"
        )

    client = TestClient(create_app(_cfg(tmp_path), password=None, allow_anonymous=True))
    data = client.get("/api/digest").json()
    assert data["items"][0]["plays"] == 4200
    assert data["items"][0]["likes"] == 310
    assert data["items"][0]["reposts"] == 45
    assert data["items"][0]["comments"] == 12
