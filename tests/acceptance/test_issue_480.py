from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.database import TRACK_SORT_FIELDS, query_tracks, taste_model_info

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "test-taste-pw"
AUTH = ("sc", PASSWORD)
SCORES = {"a": 0.87, "b": None, "c": 0.42, "d": 0.71}


@pytest.fixture
def lage(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    track_db = tmp_path / "tracks.sqlite"
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"), "track_db_path": str(track_db)}
    with TrackDB(track_db) as db:
        for name, score in SCORES.items():
            rec = db.upsert_track(TrackRecord(path=f"/inbox/{name}.wav", mtime=1.0, size=1, artist=name.upper(),
                                              title=f"Titel {name}", status=TrackStatus.INBOX))
            db.db.execute("UPDATE tracks SET taste_score = ? WHERE id = ?", (score, rec.id))
        db.db.commit()
    return cfg, track_db


def _modell(track_db):
    with TrackDB(track_db) as db:
        db.db.execute("INSERT INTO taste_model VALUES (1, 'maest', '2026-11-06 07:05:00', 12, 9, '{}')")
        db.db.commit()


def _artists(result):
    return [item["artist"] for item in result.items]


def test_sortierung_und_feld(lage):
    _, track_db = lage
    assert "taste" in TRACK_SORT_FIELDS
    desc = query_tracks(track_db, sort="taste", descending=True)
    assert _artists(desc) == ["A", "D", "C", "B"], "ohne Score zuletzt"
    assert _artists(query_tracks(track_db, sort="taste")) == ["C", "D", "A", "B"]
    assert {i["artist"]: i["taste_score"] for i in desc.items} == {k.upper(): v for k, v in SCORES.items()}


def test_filter_taste_min(lage):
    _, track_db = lage
    r = query_tracks(track_db, taste_min=0.7, sort="taste", descending=True)
    assert r.total == 2 and _artists(r) == ["A", "D"]
    assert query_tracks(track_db, taste_min=0.0).total == 3


def test_alte_db_ohne_spalte(lage):
    _, track_db = lage
    with TrackDB(track_db) as db:
        db.db.execute("ALTER TABLE tracks DROP COLUMN taste_score")
        db.db.commit()
    # TrackDB nicht erneut öffnen: query_tracks liest nur und migriert nicht.
    assert query_tracks(track_db, taste_min=0.5).total == 0
    r = query_tracks(track_db, sort="taste", descending=True)
    assert _artists(r) == ["A", "B", "C", "D"]
    assert all(i["taste_score"] is None for i in r.items)


def test_taste_model_info(lage, tmp_path):
    _, track_db = lage
    assert taste_model_info(track_db) is None
    _modell(track_db)
    assert taste_model_info(track_db) == {"embedding_model": "maest", "trained_at": "2026-11-06 07:05:00",
                                          "n_pos": 12, "n_neg": 9}
    assert taste_model_info(tmp_path / "gibt-es-nicht.sqlite") is None


def test_api_tracks(lage):
    cfg, _ = lage
    client = TestClient(create_app(cfg, password=PASSWORD))
    r = client.get("/api/tracks?taste_min=0.7&sort=taste&dir=desc", auth=AUTH)
    assert r.status_code == 200
    data = r.json()
    assert [i["artist"] for i in data["items"]] == ["A", "D"]
    assert data["items"][0]["taste_score"] == pytest.approx(0.87)


def test_tracks_seite(lage):
    cfg, track_db = lage
    client = TestClient(create_app(cfg, password=PASSWORD))
    html = client.get("/tracks", auth=AUTH).text
    assert "Taste-Modell: noch nicht trainiert" in html
    assert 'name="taste_min"' in html and "Min. Taste" in html
    assert 'href="/tracks?status=inbox&amp;taste_min=0.7"' in html
    assert "sort=taste" in html
    assert html.count("data-taste>") == 4
    assert "87 %" in html and "42 %" in html and "71 %" in html

    _modell(track_db)
    html = client.get("/tracks?taste_min=0.7&sort=taste&dir=desc", auth=AUTH).text
    assert "Taste-Modell vom 2026-11-06 07:05:00 · 12 gefällt / 9 gefällt nicht" in html
    assert html.count("data-taste>") == 2
    assert html.index("87 %") < html.index("71 %")
    assert "42 %" not in html
    assert 'value="0.7"' in html
