"""Akzeptanztests: erledigt-Marke für Gates und Käufe (DB und Route, Teil 1 von #591, Issue #<N>)."""
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.web import app as web_app

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-dn-4711"
AUTH = ("sc", PASSWORD)
GATE_SC = 4711
STORE_SC = 4712


def _setup(tmp_path):
    """Track-DB mit einem Gate und einem Kauf, dazu ein Testclient auf denselben Pfaden."""
    track_db = tmp_path / "track.sqlite"
    with TrackDB(track_db) as db:
        db.upsert_gate_item(GATE_SC, "Titel", "Artist", "https://gate.example/x")
        db.upsert_store_item(STORE_SC, "Titel 2", "Artist 2", "https://store.example/y")
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(track_db),
    }
    return TestClient(web_app.create_app(cfg, password=PASSWORD)), track_db


def _done_at(track_db: Path, tabelle: str, sc_id: int):
    with TrackDB(track_db) as db:
        zeile = db.db.execute(f"SELECT done_at FROM {tabelle} WHERE sc_id = ?", (sc_id,)).fetchone()
        return None if zeile is None else zeile[0]


def test_gate_ab_haken_setzt_die_marke(tmp_path):
    client, track_db = _setup(tmp_path)
    antwort = client.post("/api/item-done", json={"kind": "gate", "sc_id": GATE_SC}, auth=AUTH)
    assert antwort.status_code == 200
    assert antwort.json()["ok"] is True
    assert antwort.json()["done_at"]
    assert _done_at(track_db, "gate_items", GATE_SC) == antwort.json()["done_at"]

def test_kauf_ab_haken_setzt_die_marke(tmp_path):
    client, track_db = _setup(tmp_path)
    antwort = client.post("/api/item-done", json={"kind": "store", "sc_id": STORE_SC}, auth=AUTH)
    assert antwort.status_code == 200
    assert antwort.json()["done_at"]
    assert _done_at(track_db, "store_items", STORE_SC) is not None


def test_marke_zuruecknehmen(tmp_path):
    client, track_db = _setup(tmp_path)
    client.post("/api/item-done", json={"kind": "gate", "sc_id": GATE_SC}, auth=AUTH)
    antwort = client.post("/api/item-done", json={"kind": "gate", "sc_id": GATE_SC, "done": False}, auth=AUTH)
    assert antwort.status_code == 200
    assert antwort.json()["done_at"] is None
    assert _done_at(track_db, "gate_items", GATE_SC) is None


def test_unbekannte_art_ist_400(tmp_path):
    client, _track_db = _setup(tmp_path)
    antwort = client.post("/api/item-done", json={"kind": "kauf", "sc_id": GATE_SC}, auth=AUTH)
    assert antwort.status_code == 400
    assert antwort.json()["ok"] is False


def test_sc_id_fehlt_ist_400(tmp_path):
    client, _track_db = _setup(tmp_path)
    antwort = client.post("/api/item-done", json={"kind": "gate"}, auth=AUTH)
    assert antwort.status_code == 400


def test_unbekannter_eintrag_ist_404(tmp_path):
    client, track_db = _setup(tmp_path)
    antwort = client.post("/api/item-done", json={"kind": "gate", "sc_id": 999999}, auth=AUTH)
    assert antwort.status_code == 404
    assert _done_at(track_db, "gate_items", GATE_SC) is None


def test_neuer_fund_oeffnet_wieder(tmp_path):
    """Der Kern von Stephans Entscheidung: upsert_* hebt eine gesetzte Marke auf."""
    client, track_db = _setup(tmp_path)
    client.post("/api/item-done", json={"kind": "gate", "sc_id": GATE_SC}, auth=AUTH)
    assert _done_at(track_db, "gate_items", GATE_SC) is not None
    with TrackDB(track_db) as db:
        db.upsert_gate_item(GATE_SC, "Titel", "Artist", "https://gate.example/x")
    assert _done_at(track_db, "gate_items", GATE_SC) is None
