"""Akzeptanztests: Freie Tags im Explorer setzen und filtern (Issue #675)."""
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.web import app as web_app

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-tags-4711"
AUTH = ("sc", PASSWORD)
# _same_origin verlangt bei POST-Routen einen Origin-Header (CSRF-Schutz).
ORIGIN = {"origin": "http://testserver"}


def _setup(tmp_path):
    track_db = tmp_path / "track.sqlite"
    ids = {}
    with TrackDB(track_db) as db:
        for name, titel in (("Alpha", "Erster Track"), ("Beta", "Zweiter Track")):
            rec = db.upsert_track(TrackRecord(
                path=f"/inbox/{name.lower()}.mp3",
                mtime=1.0,
                size=1000,
                artist=name,
                title=titel,
                bpm=155.0,
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
    return TestClient(web_app.create_app(cfg, password=PASSWORD)), track_db, ids


def test_tag_setzen_normalisiert_und_gibt_die_liste_zurueck(tmp_path):
    client, track_db, ids = _setup(tmp_path)
    antwort = client.post(
        f"/api/tracks/{ids['Alpha']}/tags",
        json={"tag": "  Peak   Time "},
        headers=ORIGIN,
        auth=AUTH,
    )
    assert antwort.status_code == 200
    assert antwort.json()["tag"] == "peak time"
    assert antwort.json()["tags"] == ["peak time"]
    with TrackDB(track_db) as db:
        assert db.get_track_tags(ids["Alpha"]) == ["peak time"]


def test_ungueltiger_tag_wird_mit_klartext_abgelehnt(tmp_path):
    client, _track_db, ids = _setup(tmp_path)
    antwort = client.post(
        f"/api/tracks/{ids['Alpha']}/tags",
        json={"tag": "###"},
        headers=ORIGIN,
        auth=AUTH,
    )
    assert antwort.status_code == 400
    assert "unerlaubte Zeichen" in antwort.json()["error"]


def test_unbekannter_track_gibt_404(tmp_path):
    client, _track_db, _ids = _setup(tmp_path)
    antwort = client.post(
        "/api/tracks/999999/tags",
        json={"tag": "opener"},
        headers=ORIGIN,
        auth=AUTH,
    )
    assert antwort.status_code == 404
    assert antwort.json()["error"] == "Track nicht gefunden"


def test_explorer_zeigt_chip_und_filtert_nach_tag(tmp_path):
    client, _track_db, ids = _setup(tmp_path)
    assert client.post(
        f"/api/tracks/{ids['Alpha']}/tags",
        json={"tag": "opener"},
        headers=ORIGIN,
        auth=AUTH,
    ).status_code == 200

    html = client.get("/tracks", auth=AUTH).text
    assert 'class="tag-chip"' in html
    assert "opener" in html

    gefiltert = client.get("/tracks?tag=opener", auth=AUTH).text
    assert "Erster Track" in gefiltert
    assert "Zweiter Track" not in gefiltert

    leer = client.get("/tracks?tag=gibtsnicht", auth=AUTH).text
    assert "Erster Track" not in leer
    assert "Zweiter Track" not in leer
