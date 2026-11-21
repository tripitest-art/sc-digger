"""Akzeptanztests für die Smartlists-Seite (Issue #654)."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.smartlist_page import collect_smartlists, tracks_for_smartlist

PASSWORD = "smartlist-test-password"
AUTH = ("sc-digger", PASSWORD)


def _cfg(tmp_path: Path) -> Config:
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


def test_collect_smartlists_ohne_db_ist_leer(tmp_path: Path):
    assert collect_smartlists(tmp_path / "fehlt.sqlite") == []


def test_collect_smartlists_nennt_name_regelzahl_und_trefferzahl(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.save_smartlist("opener", {"energy_max": 2})
        db.save_smartlist("peaks", {"bpm_min": 140, "bpm_max": 148, "tags_any": ["peak time"]})

    rows = collect_smartlists(db_path)

    assert [(r.name, r.rule_count) for r in rows] == [("opener", 1), ("peaks", 3)]
    assert all(r.match_count == 0 for r in rows)


def test_tracks_for_smartlist_unbekannter_name_ist_none(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.save_smartlist("peaks", {"bpm_min": 140})

    assert tracks_for_smartlist(db_path, "gibtsnicht") is None
    assert tracks_for_smartlist(db_path, "peaks") == []


def test_seite_und_api_liefern_die_gespeicherten_listen(tmp_path: Path):
    cfg = _cfg(tmp_path)
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        db.save_smartlist("peaks", {"bpm_min": 140, "bpm_max": 148})

    app = create_app(cfg, password=PASSWORD)

    with TestClient(app) as client:
        seite = client.get("/smartlists", auth=AUTH)
        assert seite.status_code == 200
        assert "Smartlists" in seite.text
        assert "peaks" in seite.text

        api = client.get("/api/smartlists", auth=AUTH)
        assert api.status_code == 200
        assert api.json() == [{"name": "peaks", "rule_count": 2, "match_count": 0}]


def test_m3u8_zu_unbekannter_liste_ist_404(tmp_path: Path):
    cfg = _cfg(tmp_path)
    with TrackDB(cfg["state"]["track_db_path"]):
        pass

    app = create_app(cfg, password=PASSWORD)

    with TestClient(app) as client:
        assert client.get("/smartlists/gibtsnicht.m3u8", auth=AUTH).status_code == 404
