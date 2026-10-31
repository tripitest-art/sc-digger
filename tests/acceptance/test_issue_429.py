"""Akzeptanztests: eigener Upload-Menüpunkt und Mehrfach-Upload."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web import app as webapp
from sc_digger.web.app import create_app
from sc_digger.web.intake import UploadResult

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-upload"
AUTH = ("sc", PASSWORD)


def client_for(tmp_path: Path, monkeypatch=None) -> TestClient:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    if monkeypatch is not None:
        monkeypatch.setattr(webapp, "process_uploaded_audio", _fake_process())
    return TestClient(create_app(cfg, password=PASSWORD))


def _fake_process():
    """Ersetzt die echte Verarbeitung (ffmpeg/librosa): alles landet in der Inbox, außer “kaputt”."""
    aufrufe: list[str] = []

    def fake(file_bytes, filename, cfg, max_bytes=None):
        aufrufe.append(filename)
        fake.aufrufe = aufrufe
        if "kaputt" in filename:
            return UploadResult(ok=False, filename=filename, status="error", message="Test-Fehler")
        return UploadResult(ok=True, filename=filename, status="inbox",
                            message="abgelegt", dest_path=f"/music/inbox/{filename}",
                            bpm=150.0, key_camelot="8A")

    fake.aufrufe = aufrufe
    return fake


# --- Der Upload wandert aus dem Digest -------------------------------------------


def test_digest_hat_keinen_uploadbereich_mehr(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "process_uploaded_audio", _fake_process())
    html = client_for(tmp_path, monkeypatch).get("/digest", auth=AUTH).text
    assert "upload-zone" not in html
    assert 'type="file"' not in html
    assert "/api/upload-intake" not in html


def test_eigene_seite_fuer_den_upload(tmp_path, monkeypatch):
    html = client_for(tmp_path, monkeypatch).get("/intake", auth=AUTH)
    assert html.status_code == 200
    assert 'type="file"' in html.text
    assert re.search(r'<input[^>]*type="file"[^>]*multiple', html.text), "Feld erlaubt nur eine Datei"
    assert "/api/upload-intake" in html.text


def test_navigation_verlinkt_die_upload_seite(tmp_path, monkeypatch):
    html = client_for(tmp_path, monkeypatch).get("/", auth=AUTH).text
    nav = html.split("<nav", 1)[1].split("</nav>", 1)[0]
    assert 'href="/intake"' in nav, nav[:400]


# --- Mehrfach-Upload über die API ------------------------------------------------


def _post(client: TestClient, dateien: list[tuple[str, bytes]], feld: str = "files"):
    return client.post(
        "/api/upload-intake",
        files=[(feld, (name, data, "audio/mpeg")) for name, data in dateien],
        headers={"origin": "http://testserver"},
        auth=AUTH,
    )


def test_zwei_dateien_in_einem_aufruf(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    antwort = _post(client, [("eins.mp3", b"a"), ("zwei.mp3", b"b")])
    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert [r["filename"] for r in daten["results"]] == ["eins.mp3", "zwei.mp3"]
    assert all(r["ok"] for r in daten["results"])
    assert daten["summary"] == {"ok": 2, "rejected": 0, "error": 0}


def test_einzelne_datei_im_alten_feld_bleibt_moeglich(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    antwort = _post(client, [("eins.mp3", b"a")], feld="file")
    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert daten["results"][0]["filename"] == "eins.mp3"
    assert daten["results"][0]["ok"] is True


def test_fehlschlag_einer_datei_stoppt_die_anderen_nicht(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    antwort = _post(client, [("gut.mp3", b"a"), ("kaputt.mp3", b"b"), ("auch-gut.mp3", b"c")])
    assert antwort.status_code == 207, antwort.text  # teilweise erfolgreich
    daten = antwort.json()
    assert daten["summary"] == {"ok": 2, "rejected": 0, "error": 1}
    assert daten["ok"] is False


def test_ohne_datei_bleibt_es_bei_400(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    antwort = client.post("/api/upload-intake", data={"x": "1"},
                          headers={"origin": "http://testserver"}, auth=AUTH)
    assert antwort.status_code == 400
    assert antwort.json()["ok"] is False


def test_herkunft_wird_weiterhin_geprueft(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    antwort = client.post("/api/upload-intake",
                          files=[("files", ("eins.mp3", b"a", "audio/mpeg"))], auth=AUTH)
    assert antwort.status_code == 403


def test_mehrere_dateien_werden_der_reihe_nach_verarbeitet(tmp_path, monkeypatch):
    fake = _fake_process()
    monkeypatch.setattr(webapp, "process_uploaded_audio", fake)
    client = client_for(tmp_path, monkeypatch)
    _post(client, [("a.mp3", b"1"), ("b.mp3", b"2"), ("c.mp3", b"3")])
    assert fake.aufrufe == ["a.mp3", "b.mp3", "c.mp3"]
