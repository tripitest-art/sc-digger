"""Akzeptanztests: Interaktive Kaufliste im Web-Dashboard (Issue #337)."""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.database import query_store_items

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "test-kaufliste-secret"


def make_test_cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def test_query_store_items_reads_data_and_feedback(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.upsert_store_item(
            sc_id=101,
            title="Raw Tool",
            artist="Viper XXL",
            purchase_url="https://label.bandcamp.com/track/raw-tool",
            purchase_title="Buy on Bandcamp",
        )
        db.upsert_store_item(
            sc_id=102,
            title="Acid Drop",
            artist="Svetec",
            purchase_url="https://beatport.com/track/acid-drop/123",
            purchase_title="Beatport",
        )
        db.set_sc_feedback(101, "like")

    res = query_store_items(db_path)
    assert res.total == 2
    item101 = next(it for it in res.items if it["sc_id"] == 101)
    assert item101["artist"] == "Viper XXL"
    assert item101["feedback"] == "like"
    assert "bandcamp" in item101["purchase_url"]

    # Filterung nach Suchbegriff
    search_res = query_store_items(db_path, search="Svetec")
    assert search_res.total == 1
    assert search_res.items[0]["sc_id"] == 102


def test_kaufliste_web_routes_and_navigation(tmp_path: Path):
    cfg = make_test_cfg(tmp_path)
    db_path = cfg["state"]["track_db_path"]
    with TrackDB(db_path) as db:
        db.upsert_store_item(
            sc_id=201,
            title="Hard Techno Anthem",
            artist="O.B.I.",
            purchase_url="https://obi.bandcamp.com/track/anthem",
            purchase_title="Bandcamp",
        )

    client = TestClient(create_app(cfg, password=PASSWORD))
    auth = ("sc", PASSWORD)

    # 1. Navigation enthält aktiven Link zu /kaufliste
    root_resp = client.get("/", auth=auth)
    assert root_resp.status_code == 200
    assert 'href="/kaufliste"' in root_resp.text
    assert 'disabled">🛒 Kaufliste</span>' not in root_resp.text

    # 2. HTML-Seite /kaufliste abrufen
    kl_resp = client.get("/kaufliste", auth=auth)
    assert kl_resp.status_code == 200
    assert "Hard Techno Anthem" in kl_resp.text
    assert "O.B.I." in kl_resp.text
    assert "obi.bandcamp.com" in kl_resp.text

    # 3. JSON-API /api/kaufliste abrufen
    api_resp = client.get("/api/kaufliste", auth=auth)
    assert api_resp.status_code == 200
    data = api_resp.json()
    assert data["total"] == 1
    assert data["items"][0]["sc_id"] == 201
