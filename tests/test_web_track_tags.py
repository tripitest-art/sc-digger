"""Tests für freie Tags im Track-Explorer (Issue #675): Route und Filter auf API- und Web-Ebene."""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.web import app as web_app
from sc_digger.web.routes.tracks import active_filter_chips

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "test-pw-tags-675"
AUTH = ("sc", PASSWORD)
ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def client_and_db(tmp_path):
    track_db = tmp_path / "track.sqlite"
    ids = {}
    with TrackDB(track_db) as db:
        for name, titel in (("Alpha", "Erster Track"), ("Beta", "Zweiter Track"), ("Gamma", "Dritter Track")):
            rec = db.upsert_track(TrackRecord(
                path=f"/inbox/{name.lower()}.mp3",
                mtime=1.0,
                size=1000,
                artist=name,
                title=titel,
                bpm=150.0,
                key_camelot="8A",
                status="inbox",
            ))
            ids[name] = rec.id
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(track_db),
        "health_db_path": str(tmp_path / "seen.sqlite"),
    }
    cfg.raw["download"] = {
        "inbox_dir": str(tmp_path / "inbox"),
        "collection_dir": str(tmp_path / "collection"),
    }
    client = TestClient(web_app.create_app(cfg, password=PASSWORD))
    return client, track_db, ids


def test_post_tag_erfolgreich_mehrere_tags_alphabetisch(client_and_db):
    client, track_db, ids = client_and_db
    tid = ids["Alpha"]

    # Erster Tag
    r1 = client.post(
        f"/api/tracks/{tid}/tags",
        json={"tag": "  Peak   Time "},
        headers=ORIGIN,
        auth=AUTH,
    )
    assert r1.status_code == 200
    assert r1.json() == {"tag": "peak time", "tags": ["peak time"]}

    # Zweiter Tag: alphabetische Sortierung in 'tags'
    r2 = client.post(
        f"/api/tracks/{tid}/tags",
        json={"tag": "Opener"},
        headers=ORIGIN,
        auth=AUTH,
    )
    assert r2.status_code == 200
    assert r2.json() == {"tag": "opener", "tags": ["opener", "peak time"]}

    # Idempotent: derselbe Tag nochmals wirft keinen Fehler
    r3 = client.post(
        f"/api/tracks/{tid}/tags",
        json={"tag": "OPENER"},
        headers=ORIGIN,
        auth=AUTH,
    )
    assert r3.status_code == 200
    assert r3.json() == {"tag": "opener", "tags": ["opener", "peak time"]}


def test_post_tag_ungueltiger_tag_abgelehnt(client_and_db):
    client, _, ids = client_and_db
    tid = ids["Alpha"]

    # Ungültige Zeichen
    r = client.post(f"/api/tracks/{tid}/tags", json={"tag": "tag!invalid?"}, headers=ORIGIN, auth=AUTH)
    assert r.status_code == 400
    assert "unerlaubte Zeichen" in r.json()["error"]

    # Leerer Tag
    r_empty = client.post(f"/api/tracks/{tid}/tags", json={"tag": "   "}, headers=ORIGIN, auth=AUTH)
    assert r_empty.status_code == 400
    assert "leer" in r_empty.json()["error"]

    # Zu lang
    r_long = client.post(f"/api/tracks/{tid}/tags", json={"tag": "a" * 45}, headers=ORIGIN, auth=AUTH)
    assert r_long.status_code == 400
    assert "länger als 40" in r_long.json()["error"]

    # Tag-Feld fehlt
    r_notag = client.post(f"/api/tracks/{tid}/tags", json={"name": "foo"}, headers=ORIGIN, auth=AUTH)
    assert r_notag.status_code == 400
    assert "Tag fehlt" in r_notag.json()["error"]


def test_post_tag_csrf_schutz_und_auth(client_and_db):
    client, _, ids = client_and_db
    tid = ids["Alpha"]

    # Ohne Origin/Referer -> 403
    r_no_origin = client.post(f"/api/tracks/{tid}/tags", json={"tag": "opener"}, auth=AUTH)
    assert r_no_origin.status_code == 403

    # Falsche Origin -> 403
    r_bad_origin = client.post(
        f"/api/tracks/{tid}/tags",
        json={"tag": "opener"},
        headers={"origin": "http://evil.com"},
        auth=AUTH,
    )
    assert r_bad_origin.status_code == 403

    # Falsche Auth -> 401
    r_bad_auth = client.post(
        f"/api/tracks/{tid}/tags",
        json={"tag": "opener"},
        headers=ORIGIN,
        auth=("sc", "falsches-pw"),
    )
    assert r_bad_auth.status_code == 401


def test_api_tracks_filter_nach_tag(client_and_db):
    client, _, ids = client_and_db
    alpha = ids["Alpha"]
    beta = ids["Beta"]

    # Tags vergeben
    client.post(f"/api/tracks/{alpha}/tags", json={"tag": "opener"}, headers=ORIGIN, auth=AUTH)
    client.post(f"/api/tracks/{beta}/tags", json={"tag": "peak time"}, headers=ORIGIN, auth=AUTH)
    client.post(f"/api/tracks/{alpha}/tags", json={"tag": "peak time"}, headers=ORIGIN, auth=AUTH)

    # GET /api/tracks ohne Filter -> alle 3 Tracks
    res_all = client.get("/api/tracks", auth=AUTH).json()
    assert res_all["total"] == 3

    # Jeder Track in der Trefferliste hat ein 'tags'-Feld
    alpha_item = next(it for it in res_all["items"] if it["id"] == alpha)
    assert alpha_item["tags"] == ["opener", "peak time"]

    # GET /api/tracks?tag=opener -> nur Alpha
    res_opener = client.get("/api/tracks?tag=opener", auth=AUTH).json()
    assert res_opener["total"] == 1
    assert res_opener["items"][0]["id"] == alpha

    # GET /api/tracks?tag=  PEAK   TIME  -> Alpha und Beta
    res_peak = client.get("/api/tracks?tag=  PEAK   TIME ", auth=AUTH).json()
    assert res_peak["total"] == 2
    res_ids = {it["id"] for it in res_peak["items"]}
    assert res_ids == {alpha, beta}

    # Tag ohne Treffer -> total: 0, items: []
    res_leer = client.get("/api/tracks?tag=unbekannt", auth=AUTH).json()
    assert res_leer["total"] == 0
    assert res_leer["items"] == []

    # Ungültiger Tag (Syntax) -> leere Trefferliste, kein Fehler
    res_invalid = client.get("/api/tracks", params={"tag": "###"}, auth=AUTH).json()
    assert res_invalid["total"] == 0
    assert res_invalid["items"] == []

    # Ungültige Zeichen in URL
    res_invalid_url = client.get("/api/tracks?tag=ungueltig!zeichen", auth=AUTH).json()
    assert res_invalid_url["total"] == 0
    assert res_invalid_url["items"] == []

    # Leerer Parameter tag= -> kein Filter
    res_empty_param = client.get("/api/tracks?tag=", auth=AUTH).json()
    assert res_empty_param["total"] == 3


def test_active_filter_chips_enthaelt_tag():
    chips = active_filter_chips({"tag": "peak time"}, limit=25)
    assert len(chips) == 1
    assert chips[0]["key"] == "tag"
    assert chips[0]["label"] == "Tag"
    assert chips[0]["value"] == "peak time"
    assert "limit=25" in chips[0]["remove_url"]
