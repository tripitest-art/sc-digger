"""Tests für den Durchhör-Modus des Web-Dashboards (/digest/durchhoeren)."""
from __future__ import annotations

import types
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web import app as web_app

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "test-durchhoeren-password"
AUTH = ("sc", PASSWORD)


def _eintrag(sc_id: int, feedback: str | None = None, title: str | None = None, artist: str | None = None):
    return {
        "sc_id": sc_id,
        "artist": artist or f"Artist {sc_id}",
        "title": title or f"Titel {sc_id}",
        "url": f"https://soundcloud.com/a/{sc_id}",
        "feedback": feedback,
        "plays": 100,
        "likes": 10,
        "status": "open",
        "download_kind": "native",
        "gate_url": None,
        "gate_done_at": None,
        "purchase_url": None,
    }


def _client(tmp_path: Path, monkeypatch, items):
    monkeypatch.setattr(
        web_app,
        "query_digest",
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


def test_durchhoeren_ohne_offene_vorschlaege_rendert_keine_datensaetze(tmp_path, monkeypatch):
    """Ohne offene Vorschläge zeigt die Seite den Leerzustand und rendert keine Datensätze."""
    client = _client(
        tmp_path,
        monkeypatch,
        [_eintrag(101, feedback="like"), _eintrag(102, feedback="dislike"), _eintrag(103, feedback="later")],
    )
    resp = client.get("/digest/durchhoeren", auth=AUTH)
    assert resp.status_code == 200
    html = resp.text
    assert 'id="durchhoeren"' in html
    assert "nichts durchzuhören" in html
    assert "data-sc-id" not in html


def test_durchhoeren_leerzustand_bei_leerer_db(tmp_path, monkeypatch):
    """Ist die Liste komplett leer, erscheint ebenfalls der Leerzustand ohne Datensätze."""
    client = _client(tmp_path, monkeypatch, [])
    resp = client.get("/digest/durchhoeren", auth=AUTH)
    assert resp.status_code == 200
    html = resp.text
    assert "nichts durchzuhören" in html
    assert "data-sc-id" not in html


def test_durchhoeren_fortschritt_drei_offene_eintraege(tmp_path, monkeypatch):
    """Mit drei offenen Einträgen lautet der Fortschritt '1 von 3' und alle drei Datensätze sind vorhanden."""
    client = _client(tmp_path, monkeypatch, [_eintrag(1), _eintrag(2), _eintrag(3)])
    resp = client.get("/digest/durchhoeren", auth=AUTH)
    assert resp.status_code == 200
    html = resp.text
    assert 'id="durchhoeren"' in html
    assert "1 von 3" in html
    assert 'data-sc-id="1"' in html
    assert 'data-sc-id="2"' in html
    assert 'data-sc-id="3"' in html


def test_durchhoeren_filterung_bereits_bewerteter_eintraege(tmp_path, monkeypatch):
    """Bereits bewertete Vorschläge (like/dislike/later) werden gefiltert, nur offene bleiben übrig."""
    client = _client(
        tmp_path,
        monkeypatch,
        [
            _eintrag(1, feedback=None),
            _eintrag(2, feedback="like"),
            _eintrag(3, feedback="dislike"),
            _eintrag(4, feedback=None),
            _eintrag(5, feedback="later"),
        ],
    )
    resp = client.get("/digest/durchhoeren", auth=AUTH)
    assert resp.status_code == 200
    html = resp.text
    assert "1 von 2" in html
    assert 'data-sc-id="1"' in html
    assert 'data-sc-id="4"' in html
    assert 'data-sc-id="2"' not in html
    assert 'data-sc-id="3"' not in html
    assert 'data-sc-id="5"' not in html


def test_digest_seite_enthaelt_link_auf_durchhoeren(tmp_path, monkeypatch):
    """Der Digest-Kopf auf /digest enthält einen Link auf den Durchhör-Modus."""
    client = _client(tmp_path, monkeypatch, [_eintrag(1)])
    resp = client.get("/digest", auth=AUTH)
    assert resp.status_code == 200
    assert 'href="/digest/durchhoeren"' in resp.text
    assert "Durchhören" in resp.text
