"""Eigene Tests für Mehrfach-Upload und Upload-Menüpunkt (Issue #429)."""
from __future__ import annotations

from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from sc_digger.models import Config
from sc_digger.web import app as webapp
from sc_digger.web.app import MAX_UPLOAD_BYTES, create_app
from sc_digger.web.intake import UploadResult

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-upload"
AUTH = ("sc", PASSWORD)
ORIGIN = {"origin": "http://testserver"}


def client_for(tmp_path: Path, monkeypatch=None) -> TestClient:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    cfg.raw["download"] = {
        **cfg["download"],
        "inbox_dir": str(tmp_path / "inbox"),
        "intake_dir": str(tmp_path / "intake"),
    }
    return TestClient(create_app(cfg, password=PASSWORD))


def test_sum_exceeds_max_bytes_returns_413(tmp_path, monkeypatch):
    calls = []

    def fake(file_bytes, filename, cfg, max_bytes=None):
        calls.append(filename)
        return UploadResult(ok=True, filename=filename, status="inbox", message="ok")

    monkeypatch.setattr(webapp, "process_uploaded_audio", fake)
    monkeypatch.setattr(webapp, "MAX_UPLOAD_BYTES", 10)
    client = client_for(tmp_path)

    # 413 wenn Summe der Dateien Grenze übersteigt
    r = client.post(
        "/api/upload-intake",
        files=[("files", ("a.wav", b"12345678", "audio/wav")), ("files", ("b.wav", b"12345678", "audio/wav"))],
        headers=ORIGIN,
        auth=AUTH,
    )
    assert r.status_code == 413
    assert r.json()["ok"] is False
    assert len(calls) == 0


def test_too_large_single_file_does_not_abort_other_files(tmp_path, monkeypatch):
    calls = []

    def fake(file_bytes, filename, cfg, max_bytes=None):
        calls.append(filename)
        if filename == "huge.wav":
            return UploadResult(
                ok=False, filename=filename, status="too_large", message="Datei zu groß (1.5 GB)"
            )
        return UploadResult(ok=True, filename=filename, status="inbox", message="abgelegt")

    monkeypatch.setattr(webapp, "process_uploaded_audio", fake)
    client = client_for(tmp_path)

    r = client.post(
        "/api/upload-intake",
        files=[
            ("files", ("first.wav", b"a", "audio/wav")),
            ("files", ("huge.wav", b"b", "audio/wav")),
            ("files", ("last.wav", b"c", "audio/wav")),
        ],
        headers=ORIGIN,
        auth=AUTH,
    )
    # 207 da mindestens eine ok und mindestens eine nicht ok
    assert r.status_code == 207
    body = r.json()
    assert body["ok"] is False
    assert body["summary"] == {"ok": 2, "rejected": 0, "error": 1}
    assert [res["status"] for res in body["results"]] == ["inbox", "too_large", "inbox"]
    assert calls == ["first.wav", "huge.wav", "last.wav"]


def test_status_code_422_when_all_files_fail(tmp_path, monkeypatch):
    def fake(file_bytes, filename, cfg, max_bytes=None):
        if filename == "bad_quality.mp3":
            return UploadResult(ok=False, filename=filename, status="rejected", message="128 kbps")
        return UploadResult(ok=False, filename=filename, status="error", message="Korrupt")

    monkeypatch.setattr(webapp, "process_uploaded_audio", fake)
    client = client_for(tmp_path)

    r = client.post(
        "/api/upload-intake",
        files=[
            ("files", ("bad_quality.mp3", b"x", "audio/mpeg")),
            ("files", ("corrupt.wav", b"y", "audio/wav")),
        ],
        headers=ORIGIN,
        auth=AUTH,
    )
    assert r.status_code == 422
    body = r.json()
    assert body["ok"] is False
    assert body["summary"] == {"ok": 0, "rejected": 1, "error": 1}


def test_html_fallback_on_form_document_submission(tmp_path, monkeypatch):
    def fake(file_bytes, filename, cfg, max_bytes=None):
        return UploadResult(ok=True, filename=filename, status="inbox", message="abgelegt", bpm=150.0, key_camelot="8A")

    monkeypatch.setattr(webapp, "process_uploaded_audio", fake)
    client = client_for(tmp_path)

    r = client.post(
        "/api/upload-intake",
        files=[("files", ("test.wav", b"RIFF", "audio/wav"))],
        headers={**ORIGIN, "sec-fetch-dest": "document"},
        auth=AUTH,
    )
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "1 erfolgreich" in r.text
    assert "test.wav" in r.text


def test_files_and_file_combined(tmp_path, monkeypatch):
    order = []

    def fake(file_bytes, filename, cfg, max_bytes=None):
        order.append(filename)
        return UploadResult(ok=True, filename=filename, status="inbox", message="abgelegt")

    monkeypatch.setattr(webapp, "process_uploaded_audio", fake)
    client = client_for(tmp_path)

    r = client.post(
        "/api/upload-intake",
        files=[
            ("file", ("legacy.mp3", b"1", "audio/mpeg")),
            ("files", ("multi_1.mp3", b"2", "audio/mpeg")),
            ("files", ("multi_2.mp3", b"3", "audio/mpeg")),
        ],
        headers=ORIGIN,
        auth=AUTH,
    )
    assert r.status_code == 200
    assert r.json()["summary"] == {"ok": 3, "rejected": 0, "error": 0}
    assert order == ["legacy.mp3", "multi_1.mp3", "multi_2.mp3"]
