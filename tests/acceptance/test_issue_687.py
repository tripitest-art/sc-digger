"""Akzeptanztests: Durchhör-Modus für den Digest (Issue #687)."""
import types
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web import app as web_app

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-durchhoeren-4711"
AUTH = ("sc", PASSWORD)


def _eintrag(sc_id, feedback=None):
    return {
        "sc_id": sc_id, "artist": f"Artist {sc_id}", "title": f"Titel {sc_id}",
        "url": f"https://soundcloud.com/a/{sc_id}", "feedback": feedback,
        "plays": 1, "likes": 1, "status": "open", "download_kind": "native",
        "gate_url": None, "gate_done_at": None, "purchase_url": None,
    }


def _client(tmp_path, monkeypatch, items):
    monkeypatch.setattr(
        web_app, "query_digest",
        lambda *a, **k: types.SimpleNamespace(items=items, total=len(items)),
    )
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
        "health_db_path": str(tmp_path / "seen.sqlite"),
    }
    cfg.raw["download"] = {
        "inbox_dir": str(tmp_path / "inbox"),
        "collection_dir": str(tmp_path / "collection"),
    }
    return TestClient(web_app.create_app(cfg, password=PASSWORD))


def test_seite_zeigt_nur_unbewertete_vorschlaege(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, [_eintrag(4711), _eintrag(4712, feedback="like")])
    html = client.get("/digest/durchhoeren", auth=AUTH).text
    assert 'id="durchhoeren"' in html
    assert 'data-sc-id="4711"' in html
    assert "Titel 4711" in html
    assert 'data-sc-id="4712"' not in html


def test_ohne_offene_vorschlaege(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, [_eintrag(4711, feedback="dislike")])
    html = client.get("/digest/durchhoeren", auth=AUTH).text
    assert "nichts durchzuhören" in html


def test_seite_bewertet_ueber_die_bestehende_route(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, [_eintrag(4711), _eintrag(4712)])
    html = client.get("/digest/durchhoeren", auth=AUTH).text
    assert "/api/feedback" in html
    assert "1 von 2" in html
