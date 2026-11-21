"""Unit-Tests für die Smartlists-Webseite und M3U8-Export (Issue #654)."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.smartlist_page import (
    DEFAULT_PROFILE,
    SmartlistRow,
    collect_smartlists,
    smartlist_m3u8,
    tracks_for_smartlist,
)

PASSWORD = "test-smartlists-pw"
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


def test_smartlist_constants_und_row():
    assert DEFAULT_PROFILE == "schranz"
    row = SmartlistRow(name="test", rule_count=2, match_count=5)
    assert row.name == "test"
    assert row.rule_count == 2
    assert row.match_count == 5


def test_collect_smartlists_fehlende_db_oder_tabelle_loggt_warnung_ohne_pfad(tmp_path: Path, caplog):
    caplog.set_level(logging.WARNING)
    missing_file = tmp_path / "nicht_da.sqlite"

    res = collect_smartlists(missing_file)
    assert res == []

    # Prüfen, dass kein Pfad und kein interner Fehlertext im Log steht
    for record in caplog.records:
        assert str(missing_file) not in record.message
        assert "OperationalError" not in record.message
        assert "Traceback" not in record.message

    caplog.clear()

    # DB existiert, aber Tabelle smartlists fehlt
    empty_db = tmp_path / "empty.sqlite"
    conn = sqlite3.connect(empty_db)
    conn.execute("CREATE TABLE foo (id INT)")
    conn.commit()
    conn.close()

    res2 = collect_smartlists(empty_db)
    assert res2 == []
    for record in caplog.records:
        assert str(empty_db) not in record.message


def test_collect_smartlists_sortierung_und_profil(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path, profile_id="schranz") as db:
        db.save_smartlist("b_liste", {"bpm_min": 140})
        db.save_smartlist("A_liste", {"bpm_min": 130})
        db.save_smartlist("c_liste", {"bpm_min": 150})

    # Liste in anderem Profil anlegen
    with TrackDB(db_path, profile_id="techno") as db:
        db.save_smartlist("techno_liste", {"bpm_min": 135})

    # schranz Profil (Standard)
    rows = collect_smartlists(db_path)
    assert [r.name for r in rows] == ["A_liste", "b_liste", "c_liste"]

    # techno Profil
    rows_techno = collect_smartlists(db_path, profile_id="techno")
    assert [r.name for r in rows_techno] == ["techno_liste"]


def test_collect_smartlists_zaehlt_treffer(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.upsert_track(TrackRecord(
            path="/music/t1.mp3",
            mtime=1.0,
            size=100,
            artist="Artist 1",
            title="Track 1",
            bpm=145.0,
            key_camelot="8A",
            status="archive",
        ))
        db.upsert_track(TrackRecord(
            path="/music/t2.mp3",
            mtime=1.0,
            size=100,
            artist="Artist 2",
            title="Track 2",
            bpm=155.0,
            key_camelot="9A",
            status="archive",
        ))
        db.save_smartlist("fast", {"bpm_min": 150})
        db.save_smartlist("all_schranz", {"bpm_min": 140})

    rows = collect_smartlists(db_path)
    row_map = {r.name: r for r in rows}
    assert row_map["fast"].rule_count == 1
    assert row_map["fast"].match_count == 1
    assert row_map["all_schranz"].match_count == 2


def test_collect_smartlists_ungueltige_regeln_im_json(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        # Rohe Zeile mit kaputtem JSON einfügen
        db.db.execute(
            "INSERT INTO smartlists (profile_id, name, rules_json) VALUES ('schranz', 'broken', 'kein json')"
        )
        # Rohe Zeile mit ungültigen Regelwerten einfügen (z.B. taste_min > 1)
        db.db.execute(
            "INSERT INTO smartlists (profile_id, name, rules_json) VALUES ('schranz', 'invalid', '{\"taste_min\": 99}')"
        )
        db.db.commit()

    rows = collect_smartlists(db_path)
    assert len(rows) == 2
    row_map = {r.name: r for r in rows}
    assert row_map["broken"].rule_count == 0
    assert row_map["broken"].match_count == 0
    assert row_map["invalid"].rule_count == 1
    assert row_map["invalid"].match_count == 0


def test_tracks_for_smartlist_erfolgreich_und_limit(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.upsert_track(TrackRecord(
            path="/music/t1.mp3",
            mtime=1.0,
            size=100,
            artist="Artist 1",
            title="Track 1",
            bpm=145.0,
            key_camelot="8A",
            status="archive",
        ))
        db.upsert_track(TrackRecord(
            path="/music/t2.mp3",
            mtime=1.0,
            size=100,
            artist="Artist 2",
            title="Track 2",
            bpm=150.0,
            key_camelot="9A",
            status="archive",
        ))
        db.save_smartlist("listen", {"bpm_min": 140})

    # Alle Treffer
    tracks = tracks_for_smartlist(db_path, "listen")
    assert tracks is not None
    assert len(tracks) == 2
    assert [t.path for t in tracks] == ["/music/t1.mp3", "/music/t2.mp3"]

    # Mit Limit
    limited = tracks_for_smartlist(db_path, "listen", limit=1)
    assert limited is not None
    assert len(limited) == 1
    assert limited[0].path == "/music/t1.mp3"


def test_tracks_for_smartlist_fehlerfaelle(tmp_path: Path):
    assert tracks_for_smartlist(tmp_path / "fehlt.sqlite", "liste") is None

    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        # Kaputte Regeln
        db.db.execute(
            "INSERT INTO smartlists (profile_id, name, rules_json) VALUES ('schranz', 'kaputt', '{\"taste_min\": 99}')"
        )
        db.db.commit()

    assert tracks_for_smartlist(db_path, "kaputt") == []
    assert tracks_for_smartlist(db_path, "unbekannt") is None


def test_smartlist_m3u8():
    empty = smartlist_m3u8([])
    assert empty == "#EXTM3U\n"

    tracks = [
        TrackRecord(path="/music/track1.mp3", mtime=1.0, size=10),
        TrackRecord(path="/music/sub/track2.wav", mtime=1.0, size=20),
    ]
    res = smartlist_m3u8(tracks)
    expected = "#EXTM3U\n/music/track1.mp3\n/music/sub/track2.wav\n"
    assert res == expected


def test_m3u8_route_exportiert_korrekten_inhalt(tmp_path: Path):
    cfg = _cfg(tmp_path)
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        db.upsert_track(TrackRecord(
            path="/collection/t1.flac",
            mtime=1.0,
            size=100,
            artist="Artist",
            title="Title",
            bpm=142.0,
            status="archive",
        ))
        db.save_smartlist("my_list", {"bpm_min": 140})

    app = create_app(cfg, password=PASSWORD)
    with TestClient(app) as client:
        resp = client.get("/smartlists/my_list.m3u8", auth=AUTH)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("audio/x-mpegurl")
        assert resp.text == "#EXTM3U\n/collection/t1.flac\n"
