"""Eigene Tests zur rein lesenden Track-Abfrage-Schicht des Dashboards (Issue #220).

Ergänzen die Akzeptanztests um Filterkombinationen, Wildcard-Escaping, Paginierung,
den Umgang mit fehlender Datei/Tabelle und den Nachweis, dass Abfragen nicht schreiben.
"""
import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.database import (
    CollectionOverview,
    TrackSearchResult,
    get_collection_overview,
    query_tracks,
)

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-220"


def make_cfg(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def client_for(tmp_path):
    return TestClient(create_app(make_cfg(tmp_path), password=PASSWORD))


def seed(tmp_path):
    """Vier Tracks: zwei Archiv (Svetec), einer Inbox, einer Rejected."""
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.upsert_track(TrackRecord(
            path="/music/Schranz/150/a.wav", mtime=1.0, size=1000,
            artist="Svetec", title="Acid Kick", format="wav", bitrate_kbps=1411.0,
            quality_status=QualityStatus.OK, bpm=150.0, key_camelot="8A",
            status=TrackStatus.ARCHIVE,
        ))
        db.upsert_track(TrackRecord(
            path="/music/Schranz/155/b.flac", mtime=2.0, size=2000,
            artist="Svetec", title="Raw 100%", format="flac", bitrate_kbps=900.0,
            quality_status=QualityStatus.OK, bpm=155.0, key_camelot="8A",
            status=TrackStatus.ARCHIVE, adopted_at="2026-10-01 10:00:00",
        ))
        db.upsert_track(TrackRecord(
            path="/music/inbox/c.mp3", mtime=3.0, size=500,
            artist="Viper XXL", title="Hard Groove", format="mp3", bitrate_kbps=320.0,
            quality_status=QualityStatus.CLIPPED, bpm=160.0, key_camelot="9A",
            status=TrackStatus.INBOX,
        ))
        db.upsert_track(TrackRecord(
            path="/music/rejected/d.mp3", mtime=4.0, size=200,
            artist="Fake DJ", title="Low Quality", format="mp3",
            quality_status=QualityStatus.FAKE_TRANSCODE, bpm=140.0,
            status=TrackStatus.REJECTED,
        ))
    return db_path


# --------------------------------------------------------------- Übersicht
def test_overview_counts_and_distributions(tmp_path):
    ov = get_collection_overview(seed(tmp_path))
    assert ov.total_tracks == 4
    assert ov.archive_count == 2
    assert ov.inbox_count == 1
    assert ov.rejected_count == 1
    assert ov.adopted_count == 1
    assert ov.quality_counts == {"ok": 2, "clipped": 1, "fake_transcode": 1}
    assert ov.format_counts == {"wav": 1, "flac": 1, "mp3": 2}


def test_overview_to_dict_is_json_ready(tmp_path):
    data = get_collection_overview(seed(tmp_path)).to_dict()
    assert set(data) == {
        "total_tracks", "archive_count", "inbox_count", "rejected_count",
        "adopted_count", "quality_counts", "format_counts",
    }
    json.dumps(data)


# --------------------------------------------------------------- Filter
def test_query_default_returns_all_sorted_by_id(tmp_path):
    res = query_tracks(seed(tmp_path))
    assert res.total == 4
    ids = [item["id"] for item in res.items]
    assert ids == sorted(ids)
    assert set(res.items[0]) >= {"id", "path", "artist", "title", "bpm", "status"}


def test_query_multiword_search_requires_all_words(tmp_path):
    res = query_tracks(seed(tmp_path), search="svetec raw")
    assert res.total == 1
    assert res.items[0]["title"] == "Raw 100%"


def test_query_search_wildcard_is_literal(tmp_path):
    # "%" darf nicht als LIKE-Wildcard alles finden, sondern nur das Prozent-Zeichen.
    res = query_tracks(seed(tmp_path), search="%")
    assert res.total == 1
    assert res.items[0]["title"] == "Raw 100%"


def test_query_bpm_range(tmp_path):
    res = query_tracks(seed(tmp_path), bpm_min=150.0, bpm_max=155.0)
    assert res.total == 2
    assert sorted(item["bpm"] for item in res.items) == [150.0, 155.0]


def test_query_adopted_false_returns_not_adopted(tmp_path):
    res = query_tracks(seed(tmp_path), adopted=False)
    assert res.total == 3
    assert all(item["adopted_at"] is None for item in res.items)


def test_query_quality_and_key_filters(tmp_path):
    db_path = seed(tmp_path)
    assert query_tracks(db_path, quality_status="fake_transcode").total == 1
    assert query_tracks(db_path, key_camelot="8A").total == 2
    assert query_tracks(db_path, key_camelot="ZZ").total == 0


def test_query_limit_offset_keeps_total(tmp_path):
    db_path = seed(tmp_path)
    all_ids = [item["id"] for item in query_tracks(db_path).items]
    page = query_tracks(db_path, limit=2, offset=1)
    assert page.total == 4                       # total zählt alle Treffer, nicht die Seite
    assert [item["id"] for item in page.items] == all_ids[1:3]


def test_query_negative_limit_is_empty(tmp_path):
    res = query_tracks(seed(tmp_path), limit=-5)
    assert res.total == 4
    assert res.items == []


# --------------------------------------------------------------- Fehlende Datei/Tabelle
def test_missing_db_is_empty_and_creates_no_file(tmp_path):
    missing = tmp_path / "nope.sqlite"
    overview = get_collection_overview(missing)
    res = query_tracks(missing)
    assert overview == CollectionOverview()
    assert isinstance(res, TrackSearchResult)
    assert res.total == 0 and res.items == []
    assert not missing.exists()


def test_db_without_tracks_table_is_empty(tmp_path):
    db_path = tmp_path / "empty.sqlite"
    sqlite3.connect(str(db_path)).close()
    assert get_collection_overview(db_path).total_tracks == 0
    assert query_tracks(db_path).items == []


def test_queries_do_not_modify_db_file(tmp_path):
    db_path = seed(tmp_path)
    before = db_path.read_bytes()
    get_collection_overview(db_path)
    query_tracks(db_path, search="Svetec")
    query_tracks(db_path, adopted=True)
    assert db_path.read_bytes() == before


# --------------------------------------------------------------- API
def test_api_requires_auth(tmp_path):
    seed(tmp_path)
    client = client_for(tmp_path)
    assert client.get("/api/database/overview").status_code == 401
    assert client.get("/api/tracks").status_code == 401


def test_api_overview_and_tracks_json(tmp_path):
    seed(tmp_path)
    client = client_for(tmp_path)
    ov = client.get("/api/database/overview", auth=("sc", PASSWORD))
    assert ov.status_code == 200
    assert ov.json()["total_tracks"] == 4
    body = client.get("/api/tracks?q=svetec&status=archive", auth=("sc", PASSWORD)).json()
    assert body["total"] == 2
    assert all(item["artist"] == "Svetec" for item in body["items"])


def test_api_tracks_accepts_filter_params(tmp_path):
    seed(tmp_path)
    client = client_for(tmp_path)
    body = client.get(
        "/api/tracks?bpm_min=150&bpm_max=155&adopted=1&key_camelot=8A",
        auth=("sc", PASSWORD),
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Raw 100%"


def test_api_tracks_limit_and_offset(tmp_path):
    seed(tmp_path)
    client = client_for(tmp_path)
    body = client.get("/api/tracks?limit=1&offset=1", auth=("sc", PASSWORD)).json()
    assert body["total"] == 4
    assert len(body["items"]) == 1


def test_api_tracks_ignores_invalid_query_values(tmp_path):
    seed(tmp_path)
    client = client_for(tmp_path)
    body = client.get("/api/tracks?bpm_min=abc&adopted=vielleicht", auth=("sc", PASSWORD)).json()
    assert body["total"] == 4
