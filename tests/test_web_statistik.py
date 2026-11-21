"""Tests für die Web-Statistik-Seite und den Trend-Radar (Issue #658)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.statistik import MAX_DAYS, MIN_DAYS, collect_statistics

PASSWORD = "test-statistik-password"
AUTH = ("sc-digger", PASSWORD)


def _make_cfg(tmp_path: Path) -> Config:
    return Config(raw={
        "state": {
            "db_path": str(tmp_path / "seen.sqlite"),
            "track_db_path": str(tmp_path / "tracks.sqlite"),
            "health_db_path": str(tmp_path / "seen.sqlite"),
        },
        "download": {
            "inbox_dir": str(tmp_path / "inbox"),
            "collection_dir": str(tmp_path / "collection"),
        },
    })


def test_collect_statistics_fehlende_dateien_erzeugen_keine_dateien(tmp_path: Path):
    track_db = tmp_path / "nicht_da_tracks.sqlite"
    state_db = tmp_path / "nicht_da_state.sqlite"

    daten = collect_statistics(track_db, state_db, days=7)

    assert not track_db.exists()
    assert not state_db.exists()
    assert daten["days"] == 7
    assert daten["funnel"] == {
        "runs_total": 0,
        "runs_ok": 0,
        "tracks_scanned": 0,
        "tracks_inbox": 0,
        "tracks_rejected": 0,
        "likes": 0,
        "dislikes": 0,
        "later": 0,
        "top_artists": [],
    }
    assert daten["trends"] == {"artists": [], "tracks": [], "tags": []}


@pytest.mark.parametrize("invalid_days", [0, -1, 366, 1000, "7", None, True, False, 3.5])
def test_collect_statistics_validiert_days(tmp_path: Path, invalid_days):
    with pytest.raises(ValueError):
        collect_statistics(tmp_path / "tracks.sqlite", tmp_path / "seen.sqlite", days=invalid_days)


def test_collect_statistics_erlaubte_grenzwerte(tmp_path: Path):
    d_min = collect_statistics(tmp_path / "t.sqlite", tmp_path / "s.sqlite", days=MIN_DAYS)
    assert d_min["days"] == 1
    d_max = collect_statistics(tmp_path / "t.sqlite", tmp_path / "s.sqlite", days=MAX_DAYS)
    assert d_max["days"] == 365


def test_collect_statistics_mit_daten(tmp_path: Path):
    track_db = tmp_path / "tracks.sqlite"
    state_db = tmp_path / "seen.sqlite"

    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    past_str = (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect(state_db) as db:
        db.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, mode TEXT, ok INTEGER, found INTEGER, finished_at TEXT, error TEXT)")
        db.execute("INSERT INTO runs VALUES (1, 'discover', 1, 50, ?, NULL)", (now_str,))
        db.execute("INSERT INTO runs VALUES (2, 'discover', 0, 10, ?, 'error')", (past_str,))

    with sqlite3.connect(track_db) as db:
        db.execute("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY,
                artist TEXT,
                title TEXT,
                status TEXT,
                quality_status TEXT,
                created_at TEXT
            )
        """)
        db.execute("INSERT INTO tracks VALUES (1, 'Artist A', 'Track 1', 'inbox', 'ok', ?)", (now_str,))
        db.execute("INSERT INTO tracks VALUES (2, 'Artist A', 'Track 2', 'inbox', 'ok', ?)", (now_str,))
        db.execute("INSERT INTO tracks VALUES (3, 'Artist B', 'Track 3', 'rejected', 'fake', ?)", (now_str,))

        db.execute("CREATE TABLE sc_feedback (sc_id INTEGER, value TEXT, updated_at TEXT)")
        db.execute("INSERT INTO sc_feedback VALUES (101, 'like', ?)", (now_str,))
        db.execute("INSERT INTO sc_feedback VALUES (102, 'dislike', ?)", (now_str,))
        db.execute("INSERT INTO sc_feedback VALUES (103, 'later', ?)", (now_str,))

        db.execute("""
            CREATE TABLE track_snapshots (
                id INTEGER PRIMARY KEY,
                sc_id INTEGER,
                artist TEXT,
                title TEXT,
                likes INTEGER,
                plays INTEGER,
                reposts INTEGER,
                comments INTEGER,
                tags TEXT,
                recorded_at TEXT
            )
        """)
        # Snapshot vor 3 Tagen
        snap_start = (now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "INSERT INTO track_snapshots VALUES (1, 201, 'Artist T', 'Trending Track', 20, 100, 2, 1, '[\"schranz\", \"techno\"]', ?)",
            (snap_start,),
        )
        # Snapshot heute
        db.execute(
            "INSERT INTO track_snapshots VALUES (2, 201, 'Artist T', 'Trending Track', 40, 200, 5, 2, '[\"schranz\", \"techno\"]', ?)",
            (now_str,),
        )

        # Zweiter Track mit demselben Tag für calculate_tag_trends (min_tracks=2)
        db.execute(
            "INSERT INTO track_snapshots VALUES (3, 202, 'Artist Other', 'Other Track', 20, 50, 1, 0, '[\"schranz\"]', ?)",
            (snap_start,),
        )
        db.execute(
            "INSERT INTO track_snapshots VALUES (4, 202, 'Artist Other', 'Other Track', 30, 80, 2, 1, '[\"schranz\"]', ?)",
            (now_str,),
        )

    daten = collect_statistics(track_db, state_db, days=7)
    assert daten["days"] == 7
    assert daten["funnel"]["runs_total"] == 2
    assert daten["funnel"]["runs_ok"] == 1
    assert daten["funnel"]["tracks_scanned"] == 60
    assert daten["funnel"]["tracks_inbox"] == 2
    assert daten["funnel"]["tracks_rejected"] == 1
    assert daten["funnel"]["likes"] == 1
    assert daten["funnel"]["dislikes"] == 1
    assert daten["funnel"]["later"] == 1
    assert ("Artist A", 2) in daten["funnel"]["top_artists"]

    # Trends prüfen
    assert len(daten["trends"]["tracks"]) >= 1
    tr = daten["trends"]["tracks"][0]
    assert tr["artist"] == "Artist T"
    assert tr["title"] == "Trending Track"
    assert tr["delta_likes"] == 20
    assert tr["growth_rate"] == 1.0

    assert len(daten["trends"]["artists"]) >= 1
    ar = daten["trends"]["artists"][0]
    assert ar["name"] in ("Artist T", "Artist Other")
    assert "delta" in ar
    assert "growth_rate" in ar

    # Tag trends
    assert len(daten["trends"]["tags"]) >= 1
    tag = daten["trends"]["tags"][0]
    assert tag["tag"] == "schranz"
    assert tag["delta"] > 0
    assert "growth_rate" in tag


def test_statistik_web_routen(tmp_path: Path):
    cfg = _make_cfg(tmp_path)
    app = create_app(cfg, password=PASSWORD)

    with TestClient(app) as client:
        # Auth erforderlich
        unauth = client.get("/statistik")
        assert unauth.status_code == 401
        unauth_api = client.get("/api/statistik")
        assert unauth_api.status_code == 401

        # Standard-Aufruf ohne days -> 7 Tage
        res_default = client.get("/statistik", auth=AUTH)
        assert res_default.status_code == 200
        assert "7 Tage" in res_default.text
        assert "Pipeline-Funnel" in res_default.text

        # Aufruf mit days=30
        res_30 = client.get("/statistik?days=30", auth=AUTH)
        assert res_30.status_code == 200
        assert "30 Tage" in res_30.text

        # Ungültige Werte für /statistik -> 400 PlainText
        err1 = client.get("/statistik?days=0", auth=AUTH)
        assert err1.status_code == 400
        assert err1.text == "Zeitfenster muss zwischen 1 und 365 Tagen liegen."

        err2 = client.get("/statistik?days=400", auth=AUTH)
        assert err2.status_code == 400
        assert err2.text == "Zeitfenster muss zwischen 1 und 365 Tagen liegen."

        err3 = client.get("/statistik?days=abc", auth=AUTH)
        assert err3.status_code == 400
        assert err3.text == "Zeitfenster muss zwischen 1 und 365 Tagen liegen."

        # API-Aufruf Standard
        api_default = client.get("/api/statistik", auth=AUTH)
        assert api_default.status_code == 200
        assert api_default.json()["days"] == 7
        assert "funnel" in api_default.json()
        assert "trends" in api_default.json()

        # API mit Parametern
        api_30 = client.get("/api/statistik?days=30", auth=AUTH)
        assert api_30.status_code == 200
        assert api_30.json()["days"] == 30

        # Ungültige Werte für /api/statistik -> 400
        assert client.get("/api/statistik?days=0", auth=AUTH).status_code == 400
        assert client.get("/api/statistik?days=-5", auth=AUTH).status_code == 400
        assert client.get("/api/statistik?days=500", auth=AUTH).status_code == 400
        assert client.get("/api/statistik?days=invalid", auth=AUTH).status_code == 400


def test_status_seite_enthaelt_link_auf_statistik(tmp_path: Path):
    cfg = _make_cfg(tmp_path)
    app = create_app(cfg, password=PASSWORD)

    with TestClient(app) as client:
        res = client.get("/", auth=AUTH)
        assert res.status_code == 200
        assert "/statistik" in res.text
        assert "Alle Zeitfenster" in res.text
