"""Eigene Tests für den Breakdown-Filter im Track-Explorer (Issue #334).

Prüft die Randfälle, die der Akzeptanztest offen lässt: ungültige Fließkommazahlen im
Query-Parameter, die Weitergabe von ``breakdown_max`` an den Pager-Link und die
JSON-Ausgabe von ``GET /api/tracks``. Dazu der Grenzwert ``0.0``, der im Template nicht
als „nicht vorhanden“ missverstanden werden darf.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.database import query_tracks

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-334"
AUTH = ("sc", PASSWORD)


def make_cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def client_for(tmp_path: Path) -> TestClient:
    return TestClient(create_app(make_cfg(tmp_path), password=PASSWORD))


def seed_tracks(tmp_path: Path) -> None:
    """Drei Tracks: 0 %, 18 % und unbekannter Breakdown-Anteil."""
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.upsert_track(TrackRecord(
            path="audio/null.wav", mtime=1.0, size=10, status="archive",
            artist="Null", title="Null Break", bpm=155.0, breakdown_ratio=0.0,
        ))
        db.upsert_track(TrackRecord(
            path="audio/mid.wav", mtime=1.0, size=10, status="archive",
            artist="Mitte", title="Mid Break", bpm=155.0, breakdown_ratio=0.18,
        ))
        db.upsert_track(TrackRecord(
            path="audio/none.wav", mtime=1.0, size=10, status="archive",
            artist="Ohne", title="Unbekannt", bpm=155.0, breakdown_ratio=None,
        ))


def test_api_tracks_liefert_breakdown_ratio_und_filtert(tmp_path: Path):
    seed_tracks(tmp_path)
    client = client_for(tmp_path)

    alle = client.get("/api/tracks", auth=AUTH)
    assert alle.status_code == 200
    payload = alle.json()
    assert payload["total"] == 3
    nach_titel = {item["title"]: item for item in payload["items"]}
    assert nach_titel["Null Break"]["breakdown_ratio"] == 0.0
    assert nach_titel["Mid Break"]["breakdown_ratio"] == 0.18
    assert nach_titel["Unbekannt"]["breakdown_ratio"] is None

    gefiltert = client.get("/api/tracks?breakdown_max=0.2", auth=AUTH)
    assert gefiltert.status_code == 200
    assert gefiltert.json()["total"] == 2
    assert {item["title"] for item in gefiltert.json()["items"]} == {"Null Break", "Mid Break"}

    eng = client.get("/api/tracks?breakdown_max=0.1", auth=AUTH)
    assert eng.status_code == 200
    assert [item["title"] for item in eng.json()["items"]] == ["Null Break"]


def test_api_tracks_ignoriert_ungueltige_breakdown_max(tmp_path: Path):
    seed_tracks(tmp_path)
    client = client_for(tmp_path)

    for wert in ("abc", "0,2", ""):
        antwort = client.get(f"/api/tracks?breakdown_max={wert}", auth=AUTH)
        assert antwort.status_code == 200
        # Ungültig heißt: kein Filter, alle drei Tracks bleiben sichtbar.
        assert antwort.json()["total"] == 3, wert


def test_tracks_seite_ignoriert_ungueltige_breakdown_max(tmp_path: Path):
    seed_tracks(tmp_path)
    client = client_for(tmp_path)

    antwort = client.get("/tracks?breakdown_max=abc", auth=AUTH)
    assert antwort.status_code == 200
    assert "Mid Break" in antwort.text
    # Der eingegebene Wert bleibt im Formular stehen, damit er korrigiert werden kann.
    assert 'value="abc"' in antwort.text


def test_pager_link_behaelt_breakdown_max(tmp_path: Path):
    seed_tracks(tmp_path)
    client = client_for(tmp_path)

    antwort = client.get("/tracks?breakdown_max=0.2&limit=1", auth=AUTH)
    assert antwort.status_code == 200
    # Zwei Treffer bei einer Zeile pro Seite -> es gibt einen Weiter-Link.
    assert "Weiter" in antwort.text
    links = [
        part.split('href="', 1)[1].split('"', 1)[0]
        for part in antwort.text.split("<a ")[1:]
        if "/tracks?" in part
    ]
    weiter = [link for link in links if "offset=1" in link]
    assert weiter, links
    query = parse_qs(urlparse(weiter[0].replace("&amp;", "&")).query)
    assert query["breakdown_max"] == ["0.2"]
    assert query["offset"] == ["1"]


def test_breakdown_null_prozent_wird_angezeigt(tmp_path: Path):
    seed_tracks(tmp_path)
    client = client_for(tmp_path)

    antwort = client.get("/tracks", auth=AUTH)
    assert antwort.status_code == 200
    # 0.0 ist ein gültiger Wert und darf nicht als „kein Wert“ verschwinden.
    assert "0% Break" in antwort.text
    assert "18% Break" in antwort.text


def test_query_tracks_breakdown_filter_neben_bpm_bereich(tmp_path: Path):
    seed_tracks(tmp_path)
    db_path = tmp_path / "tracks.sqlite"

    mit_bpm = query_tracks(db_path, bpm_min=150.0, breakdown_ratio_max=0.2)
    assert mit_bpm.total == 2

    ohne_treffer = query_tracks(db_path, bpm_min=160.0, breakdown_ratio_max=0.2)
    assert ohne_treffer.total == 0
    assert ohne_treffer.items == []


def test_query_tracks_fehlende_datenbank(tmp_path: Path):
    ergebnis = query_tracks(tmp_path / "fehlt.sqlite", breakdown_ratio_max=0.2)
    assert ergebnis.total == 0
    assert ergebnis.items == []


def test_query_tracks_altes_schema_ohne_breakdown_spalte(tmp_path: Path):
    """Datenbanken vor Migration 7 dürfen die Liste nicht leer laufen lassen (Issue #258)."""
    db_path = tmp_path / "tracks.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE tracks ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE NOT NULL, "
            "mtime REAL NOT NULL, size INTEGER NOT NULL, artist TEXT, title TEXT, "
            "status TEXT NOT NULL DEFAULT 'archive')"
        )
        db.execute(
            "INSERT INTO tracks (path, mtime, size, artist, title) "
            "VALUES ('audio/alt.wav', 1.0, 10, 'Alt', 'Bestand')"
        )

    alle = query_tracks(db_path)
    assert alle.total == 1
    assert alle.items[0]["title"] == "Bestand"
    # Das Feld gehört zur Schnittstelle, auch wenn die Spalte fehlt.
    assert alle.items[0]["breakdown_ratio"] is None

    # Ohne Spalte gibt es keinen bekannten Wert -> der Filter liefert keine Treffer.
    gefiltert = query_tracks(db_path, breakdown_ratio_max=0.2)
    assert gefiltert.total == 0
    assert gefiltert.items == []
