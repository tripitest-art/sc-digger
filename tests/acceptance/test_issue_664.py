"""Akzeptanztests: Gate-Datei direkt in der Digest-Zeile hochladen (Issue #664)."""
import types
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.web import app as web_app
from sc_digger.web.intake import UploadResult

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-gu-4711"
AUTH = ("sc", PASSWORD)
SC = 4711


def _setup(tmp_path, monkeypatch, **felder):
    track_db = tmp_path / "track.sqlite"
    with TrackDB(track_db) as db:
        db.upsert_gate_item(SC, "Titel", "Artist", "https://gate.example/x")
    eintrag = {
        "sc_id": SC, "artist": "Artist", "title": "Titel",
        "url": "https://soundcloud.com/a/b", "feedback": None,
        "plays": 1, "likes": 1, "status": "open", "download_kind": "gate",
        "gate_url": "https://gate.example/x", "gate_done_at": None, "purchase_url": None,
    }
    eintrag.update(felder)
    monkeypatch.setattr(web_app, "query_digest",
                        lambda *a, **k: types.SimpleNamespace(items=[eintrag], total=1))
    monkeypatch.setattr(web_app, "process_uploaded_audio",
                        lambda *_a, **_k: UploadResult(ok=True, filename="track.wav",
                                                       status="inbox", message="abgelegt"))
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"),
                        "track_db_path": str(track_db)}
    return TestClient(web_app.create_app(cfg, password=PASSWORD)), track_db


def _done_at(track_db: Path):
    with TrackDB(track_db) as db:
        zeile = db.db.execute("SELECT done_at FROM gate_items WHERE sc_id = ?", (SC,)).fetchone()
        return None if zeile is None else zeile[0]


def test_gate_zeile_hat_das_dateifeld(tmp_path, monkeypatch):
    client, _db = _setup(tmp_path, monkeypatch)
    html = client.get("/digest", auth=AUTH).text
    assert 'id="gate-upload-4711"' in html
    assert 'data-sc-id="4711"' in html
    assert "Datei ablegen" in html
    assert "/api/upload-intake" in html


def test_kein_dateifeld_ohne_gate(tmp_path, monkeypatch):
    client, _db = _setup(tmp_path, monkeypatch, download_kind="native", gate_url=None)
    html = client.get("/digest", auth=AUTH).text
    assert 'id="gate-upload-4711"' not in html


def test_upload_markiert_das_gate_als_erledigt(tmp_path, monkeypatch):
    client, track_db = _setup(tmp_path, monkeypatch)
    antwort = client.post(
        "/api/upload-intake",
        files={"files": ("track.wav", b"RIFFxxxxWAVE", "audio/wav")},
        data={"sc_id": str(SC)},
        headers={"origin": "http://testserver"},
        auth=AUTH,
    )
    assert antwort.status_code == 200
    assert antwort.json().get("gate_done") is True
    assert _done_at(track_db) is not None


def test_upload_ohne_sc_id_markiert_nichts(tmp_path, monkeypatch):
    client, track_db = _setup(tmp_path, monkeypatch)
    antwort = client.post(
        "/api/upload-intake",
        files={"files": ("track.wav", b"RIFFxxxxWAVE", "audio/wav")},
        headers={"origin": "http://testserver"},
        auth=AUTH,
    )
    assert antwort.status_code == 200
    assert _done_at(track_db) is None
