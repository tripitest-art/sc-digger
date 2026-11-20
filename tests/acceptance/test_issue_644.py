"""Akzeptanztests: Häkchen für Käufe in der Kaufliste (Teil 2 von #591, Issue #<N>)."""
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.web import app as web_app
from sc_digger.web.database import query_store_items

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-kd-4711"
AUTH = ("sc", PASSWORD)
SC = 4712


def _setup(tmp_path, *, done: bool = False):
    """Track-DB mit einem Kauf, auf Wunsch schon abgehakt; Testclient auf denselben Pfaden."""
    track_db = tmp_path / "track.sqlite"
    with TrackDB(track_db) as db:
        db.upsert_store_item(SC, "Titel 2", "Artist 2", "https://store.example/y")
        if done:
            db.mark_store_item_done(SC, True)
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(track_db),
    }
    return TestClient(web_app.create_app(cfg, password=PASSWORD)), track_db


def test_query_liefert_die_marke(tmp_path):
    client, track_db = _setup(tmp_path, done=True)
    ergebnis = query_store_items(track_db)
    assert len(ergebnis.items) == 1
    assert ergebnis.items[0]["done_at"]


def test_query_ohne_marke_ist_none(tmp_path):
    client, track_db = _setup(tmp_path)
    ergebnis = query_store_items(track_db)
    assert ergebnis.items[0]["done_at"] is None


def test_seite_zeigt_den_umschalter_fuer_offene_zeile(tmp_path):
    client, _track_db = _setup(tmp_path)
    html = client.get("/kaufliste", auth=AUTH).text
    assert 'id="done-4712"' in html
    assert 'data-done-kind="store"' in html
    assert 'data-done="0"' in html
    assert "erledigt" in html
    assert 'id="done-status"' in html
    assert "/api/item-done" in html


def test_erledigte_zeile_ist_markiert(tmp_path):
    client, _track_db = _setup(tmp_path, done=True)
    html = client.get("/kaufliste", auth=AUTH).text
    assert 'data-done="1"' in html
    assert "wieder öffnen" in html
    assert "is-done" in html


def test_umschalten_schreibt_die_marke(tmp_path):
    """Der Klick selbst geht an die vorhandene Route; hier zählt, dass die Seite danach den Zustand zeigt."""
    client, track_db = _setup(tmp_path)
    antwort = client.post("/api/item-done", json={"kind": "store", "sc_id": SC}, auth=AUTH)
    assert antwort.status_code == 200
    html = client.get("/kaufliste", auth=AUTH).text
    assert 'data-done="1"' in html
