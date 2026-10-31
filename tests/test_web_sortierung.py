"""Eigene Tests für Sortierung, Filter und Zeilenmenge im Dashboard (Issue #427)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.database import (
    DEFAULT_LIMIT,
    DIGEST_SORT_FIELDS,
    MAX_LIMIT,
    TRACK_SORT_FIELDS,
    query_digest,
    query_tracks,
)

PASSWORD = "test-sort-pw"
AUTH = ("sc", PASSWORD)


@pytest.fixture
def test_cfg(tmp_path: Path) -> Config:
    root = Path(__file__).resolve().parents[1]
    cfg = Config.load(root / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


@pytest.fixture
def client(test_cfg: Config) -> TestClient:
    return TestClient(create_app(test_cfg, password=PASSWORD))


def _populate_tracks(db_path: Path) -> None:
    with TrackDB(db_path) as db:
        # Drei Tracks mit gleichem BPM, um stabile Zweitsortierung (id ASC) zu testen
        db.upsert_track(TrackRecord(
            path="/audio/1.wav", mtime=1.0, size=1000,
            artist="Beta", title="Track 1", format="wav", bitrate_kbps=320.0,
            cutoff_hz=20000, quality_status=QualityStatus.OK, bpm=150.0, key_camelot="8A",
            status=TrackStatus.ARCHIVE,
        ))
        db.upsert_track(TrackRecord(
            path="/audio/2.wav", mtime=2.0, size=1000,
            artist="Alpha", title="Track 2", format="mp3", bitrate_kbps=320.0,
            cutoff_hz=20000, quality_status=QualityStatus.CLIPPED, bpm=150.0, key_camelot="9A",
            status=TrackStatus.INBOX,
        ))
        db.upsert_track(TrackRecord(
            path="/audio/3.wav", mtime=3.0, size=1000,
            artist="Gamma", title="Track 3", format="wav", bitrate_kbps=320.0,
            cutoff_hz=20000, quality_status=QualityStatus.OK, bpm=150.0, key_camelot="4A",
            status=TrackStatus.ARCHIVE,
        ))
        db.upsert_track(TrackRecord(
            path="/audio/4.mp3", mtime=4.0, size=1000,
            artist="Delta", title="Track 4", format="mp3", bitrate_kbps=320.0,
            cutoff_hz=20000, quality_status=QualityStatus.OK, bpm=None, key_camelot=None,
            status=TrackStatus.INBOX,
        ))


def _populate_seen(db_path: Path) -> None:
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)"
        )
        entries = [
            (1, "https://soundcloud.com/t/1", "Artist C - Track X", "2026-10-01 12:00:00"),
            (2, "https://soundcloud.com/t/2", "Artist A - Track Z", "2026-10-02 12:00:00"),
            (3, "https://soundcloud.com/t/3", "Artist B - Track Y", "2026-10-03 12:00:00"),
        ]
        db.executemany("INSERT INTO seen VALUES (?, ?, ?, ?)", entries)


# --- Tests für query_tracks -----------------------------------------------------


def test_query_tracks_constants():
    assert "artist" in TRACK_SORT_FIELDS
    assert "quality" in TRACK_SORT_FIELDS
    assert MAX_LIMIT == 200
    assert DEFAULT_LIMIT == 100


def test_query_tracks_sort_stabil_bei_gleichen_werten(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    _populate_tracks(db_path)

    # Gleicher BPM bei Beta, Alpha, Gamma: Zweitsortierung id ASC hält Reihenfolge
    res_asc = query_tracks(db_path, sort="bpm", descending=False)
    ids_asc = [t["id"] for t in res_asc.items if t["bpm"] is not None]
    assert ids_asc == [1, 2, 3]

    res_desc = query_tracks(db_path, sort="bpm", descending=True)
    ids_desc = [t["id"] for t in res_desc.items if t["bpm"] is not None]
    assert ids_desc == [1, 2, 3]

    # NULL-Wert (Delta) muss bei beiden am Ende stehen
    assert res_asc.items[-1]["artist"] == "Delta"
    assert res_desc.items[-1]["artist"] == "Delta"


def test_query_tracks_sort_weisser_liste_und_unbekannter_wert(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    _populate_tracks(db_path)

    res_default = query_tracks(db_path)
    res_unknown = query_tracks(db_path, sort="unbekannt")
    assert [t["id"] for t in res_default.items] == [t["id"] for t in res_unknown.items]

    # Gültige Sortierung nach artist case-insensitive
    res_artist = query_tracks(db_path, sort="artist", descending=False)
    assert [t["artist"] for t in res_artist.items] == ["Alpha", "Beta", "Delta", "Gamma"]


def test_query_tracks_filter_plus_sort_plus_pagination(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    _populate_tracks(db_path)

    # Nur archive: Beta (1) und Gamma (3)
    res_p1 = query_tracks(
        db_path, status="archive", sort="artist", descending=False, limit=1, offset=0
    )
    assert res_p1.total == 2
    assert len(res_p1.items) == 1
    assert res_p1.items[0]["artist"] == "Beta"

    res_p2 = query_tracks(
        db_path, status="archive", sort="artist", descending=False, limit=1, offset=1
    )
    assert res_p2.total == 2
    assert len(res_p2.items) == 1
    assert res_p2.items[0]["artist"] == "Gamma"


# --- Tests für query_digest -----------------------------------------------------


def test_query_digest_constants():
    assert "date" in DIGEST_SORT_FIELDS
    assert "download_kind" in DIGEST_SORT_FIELDS
    assert "feedback" in DIGEST_SORT_FIELDS


def test_query_digest_sortierung(tmp_path: Path):
    seen_path = tmp_path / "seen.sqlite"
    track_path = tmp_path / "tracks.sqlite"
    _populate_seen(seen_path)

    res_asc = query_digest(seen_path, track_path, sort="artist", descending=False)
    artists_asc = [it.artist for it in res_asc.items]
    assert artists_asc == ["Artist A", "Artist B", "Artist C"]

    res_desc = query_digest(seen_path, track_path, sort="artist", descending=True)
    artists_desc = [it.artist for it in res_desc.items]
    assert artists_desc == ["Artist C", "Artist B", "Artist A"]


# --- Web-Routen & API -----------------------------------------------------------


def test_api_tracks_sort_parameter(client: TestClient, tmp_path: Path):
    _populate_tracks(tmp_path / "tracks.sqlite")
    r = client.get("/api/tracks?sort=artist&dir=desc", auth=AUTH)
    assert r.status_code == 200
    data = r.json()
    assert [t["artist"] for t in data["items"]] == ["Gamma", "Delta", "Beta", "Alpha"]


def test_api_digest_sort_parameter(client: TestClient, tmp_path: Path):
    _populate_seen(tmp_path / "seen.sqlite")
    r = client.get("/api/digest?sort=date&dir=asc", auth=AUTH)
    assert r.status_code == 200
    data = r.json()
    dates = [it["reported_at"] for it in data["items"]]
    assert dates == sorted(dates)


def test_tracks_html_limit_und_sort_im_formular(client: TestClient, tmp_path: Path):
    _populate_tracks(tmp_path / "tracks.sqlite")
    html = client.get("/tracks?sort=bpm&dir=desc&limit=50", auth=AUTH).text
    assert '<input type="hidden" name="sort" value="bpm">' in html
    assert '<input type="hidden" name="dir" value="desc">' in html
    assert '<option value="50" selected>50</option>' in html


def test_query_digest_nulls_last_in_beiden_richtungen(tmp_path: Path):
    """Prüft, dass Einträge ohne Wert (z.B. Artist=None) sowohl bei asc als auch bei desc am Ende stehen (NULLS LAST)."""
    seen_path = tmp_path / "seen.sqlite"
    track_path = tmp_path / "tracks.sqlite"
    with sqlite3.connect(seen_path) as db:
        db.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        # 1: Artist Z, 2: kein Artist (Titel ohne Trenner), 3: Artist A
        db.execute("INSERT INTO seen VALUES (1, 'u1', 'Zebra - Track', '2026-10-01 10:00:00')")
        db.execute("INSERT INTO seen VALUES (2, 'u2', 'KeinTrennstrich', '2026-10-02 10:00:00')")
        db.execute("INSERT INTO seen VALUES (3, 'u3', 'Alpha - Track', '2026-10-03 10:00:00')")

    res_asc = query_digest(seen_path, track_path, sort="artist", descending=False)
    artists_asc = [it.artist for it in res_asc.items]
    assert artists_asc == ["Alpha", "Zebra", None]

    res_desc = query_digest(seen_path, track_path, sort="artist", descending=True)
    artists_desc = [it.artist for it in res_desc.items]
    assert artists_desc == ["Zebra", "Alpha", None]
