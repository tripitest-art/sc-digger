"""Unit-Tests für die Auslagerung der Tracks-Routen nach sc_digger.web.routes.tracks und common.py."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.web import common
from sc_digger.web import routes as web_routes
from sc_digger.web.app import create_app
from sc_digger.web.routes import tracks as tracks_module

PASSWORD = "router-test-password"
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


def test_common_stellt_gemeinsame_helfer_bereit(tmp_path: Path):
    conn = sqlite3.connect(":memory:")
    ro = common._ReadOnlyTrackDB(conn)
    assert ro.db is conn
    conn.close()

    err = common._ScanLinkError("kaputt")
    assert isinstance(err, Exception)

    resp = common._render("intake.html", status_code=201, results=None, summary=None, ok=None)
    assert resp.status_code == 201
    assert "Upload" in resp.body.decode("utf-8")


def test_tracks_router_registriert_alle_tracks_und_duplikat_routen():
    dummy = FastAPI()
    tracks_module.register(dummy)
    registered = {
        (method, route.path)
        for route in dummy.routes
        for method in getattr(route, "methods", []) or []
    }
    expected = {
        ("GET", "/api/tracks"),
        ("GET", "/api/tracks/{track_id}/similar"),
        ("GET", "/api/audio/{track_id}"),
        ("GET", "/tracks"),
        ("GET", "/api/duplicates"),
        ("GET", "/duplicates"),
        ("GET", "/api/inbox-duplicates"),
        ("POST", "/api/inbox-duplicates/move"),
    }
    assert expected.issubset(registered)


def test_routes_paket_exportiert_bereiche_und_register():
    assert web_routes.BEREICHE == ("status", "tracks", "mix", "digest", "intake", "config")
    dummy = FastAPI()
    web_routes.register(dummy)
    paths = {route.path for route in dummy.routes}
    assert "/tracks" in paths
    assert "/api/tracks" in paths


def test_tracks_routen_lesen_aktuelles_app_state_cfg(tmp_path: Path):
    cfg1 = _make_cfg(tmp_path / "db1")
    (tmp_path / "db1").mkdir(parents=True)
    cfg2 = _make_cfg(tmp_path / "db2")
    (tmp_path / "db2").mkdir(parents=True)

    with TrackDB(cfg2["state"]["track_db_path"]) as db:
        db.upsert_track(TrackRecord(
            path="/inbox/test.mp3",
            mtime=1.0,
            size=123,
            artist="Router Artist",
            title="Router Track",
            bpm=155.0,
            key_camelot="8A",
            status="inbox",
        ))

    app = create_app(cfg1, password=PASSWORD)
    client = TestClient(app)

    r1 = client.get("/api/tracks", auth=AUTH)
    assert r1.status_code == 200
    assert r1.json()["total"] == 0

    # Nach Konfig-Wechsel auf app.state.cfg greifen die ausgelagerten Routen sofort auf die neue DB zu.
    app.state.cfg = cfg2
    r2 = client.get("/api/tracks", auth=AUTH)
    assert r2.status_code == 200
    assert r2.json()["total"] == 1
    assert r2.json()["items"][0]["artist"] == "Router Artist"


def test_create_app_behaelt_alle_routen_und_reihenfolge(tmp_path: Path):
    cfg = _make_cfg(tmp_path)
    app = create_app(
        cfg,
        password=PASSWORD,
        base_path=tmp_path / "config.yaml",
        local_path=tmp_path / "config.local.yaml",
    )
    routes = [
        (method, route.path)
        for route in app.routes
        for method in sorted(getattr(route, "methods", []) or [])
    ]
    expected = [
        ("GET", "/healthz"),
        ("GET", "/"),
        ("GET", "/api/status"),
        ("POST", "/api/rekordbox/sync"),
        ("POST", "/api/jobs/claim"),
        ("POST", "/api/jobs/{job_id}/heartbeat"),
        ("POST", "/api/jobs/{job_id}/complete"),
        ("GET", "/api/jobs/{job_id}/audio"),
        ("GET", "/api/database/overview"),
        ("GET", "/api/tracks"),
        ("GET", "/api/tracks/{track_id}/similar"),
        ("GET", "/api/audio/{track_id}"),
        ("GET", "/tracks"),
        ("GET", "/api/duplicates"),
        ("GET", "/duplicates"),
        ("GET", "/api/inbox-duplicates"),
        ("POST", "/api/inbox-duplicates/move"),
        ("GET", "/mix"),
        ("POST", "/api/set-builder"),
        ("GET", "/api/set-builder/m3u8"),
        ("POST", "/api/mix/export-xml"),
        ("POST", "/api/check-url"),
        ("GET", "/api/digest"),
        ("GET", "/digest"),
        ("GET", "/api/kaufliste"),
        ("GET", "/api/watch"),
        ("GET", "/kaufliste"),
        ("GET", "/api/rejected"),
        ("GET", "/rejected"),
        ("POST", "/api/retry"),
        ("POST", "/api/feedback"),
        ("POST", "/api/match-override"),
        ("GET", "/intake"),
        ("POST", "/api/upload-intake"),
        ("GET", "/config"),
        ("POST", "/config/preview"),
        ("POST", "/config/save"),
        ("POST", "/config/reset"),
    ]
    assert routes == expected

