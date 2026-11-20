"""Akzeptanztests: Gates im Digest abhaken (Teil 3 von #591, Issue #<N>)."""
import types
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web import app as web_app

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-gd-4711"
AUTH = ("sc", PASSWORD)
SC = 4711


def _client(tmp_path, monkeypatch, **felder):
    """Digest-Seite mit genau einem Vorschlag; die Abfrage wird ersetzt, die Seite bleibt echt."""
    eintrag = {
        "sc_id": SC,
        "artist": "Artist",
        "title": "Titel",
        "url": "https://soundcloud.com/a/b",
        "feedback": None,
        "plays": 10,
        "likes": 2,
        "status": "open",
        "download_kind": "gate",
        "gate_url": "https://gate.example/x",
        "gate_done_at": None,
        "purchase_url": None,
    }
    eintrag.update(felder)
    monkeypatch.setattr(web_app, "query_digest",
                        lambda *a, **k: types.SimpleNamespace(items=[eintrag], total=1))
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return TestClient(web_app.create_app(cfg, password=PASSWORD))


def test_offenes_gate_hat_den_umschalter(tmp_path, monkeypatch):
    html = _client(tmp_path, monkeypatch).get("/digest", auth=AUTH).text
    assert 'id="done-4711"' in html
    assert 'data-done-kind="gate"' in html
    assert 'data-done="0"' in html
    assert "erledigt" in html
    assert 'id="done-status"' in html
    assert "/api/item-done" in html


def test_erledigtes_gate_ist_markiert(tmp_path, monkeypatch):
    html = _client(tmp_path, monkeypatch, gate_done_at="2026-11-19 10:00:00").get("/digest", auth=AUTH).text
    assert 'data-done="1"' in html
    assert "wieder öffnen" in html
    assert "is-done" in html


def test_ohne_gate_kein_umschalter(tmp_path, monkeypatch):
    html = _client(tmp_path, monkeypatch, download_kind="native", gate_url=None).get("/digest", auth=AUTH).text
    assert 'id="done-4711"' not in html
