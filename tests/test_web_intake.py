"""Eigene Tests für den Web-Upload in den Eingangsordner (Issue #230).

Der Upload ist der zweite schreibende Pfad der Web-Oberfläche. ``process_uploaded_audio``
prüft die Endung, legt die Datei in ``download.intake_dir`` und gibt sie an
``main.finish_file`` weiter – denselben Weg wie ``main.run_intake``. Die Tests ersetzen
``finish_file`` durch Fakes (kein Netz, keine echte Audio-Analyse) und prüfen je Ergebnis:
Inbox, ``_rejected``, ``_rejected/clipped``, ``_fehler`` und Fehler. Dazu die Route
``POST /api/upload-intake`` (Multipart, Auth, CSRF) und die Uploadzone in ``digest.html``.
"""
from __future__ import annotations

import io

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.intake import UploadResult, process_uploaded_audio

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-230"
# _same_origin vergleicht den netloc von Origin/Referer mit dem Host-Header.
ORIGIN = {"origin": "http://testserver"}


def make_cfg(tmp_path: Path) -> Config:
    """config.yaml mit State- und Download-Pfaden in tmp_path (nichts zeigt nach /music)."""
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
    return cfg


def client_for(cfg: Config) -> TestClient:
    return TestClient(create_app(cfg, password=PASSWORD))


def fake_success(monkeypatch, *, bpm: float = 155.0, key: str = "8A"):
    """finish_file-Ersatz, der die Prüfung besteht: Datei wandert in die Inbox."""
    def _fake(t, path: Path, inbox: Path, cfg):
        inbox.mkdir(parents=True, exist_ok=True)
        target = inbox / path.name
        path.replace(target)
        t.bpm = bpm
        t.key_camelot = key
        return target

    monkeypatch.setattr("sc_digger.main.finish_file", _fake)


def fake_reject(monkeypatch, *, clipped: bool = False, reason: str = "MP3 mit nur 128 kbps"):
    """finish_file-Ersatz, der aussortiert: Datei wandert nach _rejected[/clipped]."""
    def _fake(t, path: Path, inbox: Path, cfg):
        folder = inbox / "_rejected" / ("clipped" if clipped else "")
        folder.mkdir(parents=True, exist_ok=True)
        path.replace(folder / path.name)
        t.quality_report = {"ok": False, "reason": reason, "clipped": clipped}
        return None

    monkeypatch.setattr("sc_digger.main.finish_file", _fake)


class _Boom:
    """Stream, dessen Lesen scheitert (abgebrochene Übertragung)."""

    def read(self, size: int = -1) -> bytes:
        raise OSError("Verbindung abgebrochen")


# ------------------------------------------------------- process_uploaded_audio
def test_non_audio_is_rejected_and_writes_nothing(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.raw["download"]["intake_dir"] = str(tmp_path / "gibt-es-noch-nicht")

    result = process_uploaded_audio(io.BytesIO(b"not an audio file"), "test.txt", cfg)

    assert isinstance(result, UploadResult)
    assert result.ok is False
    assert result.status == "error"
    assert "Audio" in result.message
    # Abgelehnt, bevor etwas auf die Platte kommt: auch der Eingangsordner entsteht nicht.
    assert not Path(cfg["download"]["intake_dir"]).exists()
    assert not (tmp_path / "inbox").exists()


@pytest.mark.parametrize("name", ["", "track", "track.txt", "archiv.zip", "bild.png"])
def test_non_audio_variants_are_rejected(tmp_path, name):
    cfg = make_cfg(tmp_path)

    result = process_uploaded_audio(io.BytesIO(b"irgendwas"), name, cfg)

    assert result.ok is False
    assert result.status == "error"
    assert "Audio" in result.message


def test_path_in_filename_is_stripped(tmp_path, monkeypatch):
    """„../../escape.wav“ darf den Eingangsordner nicht verlassen (Regel 4)."""
    cfg = make_cfg(tmp_path)
    fake_success(monkeypatch)

    result = process_uploaded_audio(io.BytesIO(b"RIFF"), "../../escape.wav", cfg)

    assert result.ok is True
    assert Path(result.dest_path).parent == tmp_path / "inbox"
    assert Path(result.dest_path).name == "escape.wav"
    assert not (tmp_path.parent / "escape.wav").exists()


def test_success_reports_inbox_path_bpm_and_key(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    fake_success(monkeypatch, bpm=155.0, key="8A")

    result = process_uploaded_audio(io.BytesIO(b"RIFF"), "Svetec - Acid Kick.wav", cfg)

    assert result.ok is True
    assert result.status == "inbox"
    assert result.filename == "Svetec - Acid Kick.wav"
    assert result.dest_path == str(tmp_path / "inbox" / "Svetec - Acid Kick.wav")
    assert result.bpm == 155.0
    assert result.key_camelot == "8A"
    # Artist/Titel kommen aus dem Dateinamen (track_from_file), der Text nennt beides.
    assert "Svetec" in result.message and "Acid Kick" in result.message
    assert "155 BPM" in result.message
    # Nichts bleibt im Eingang liegen.
    assert list((tmp_path / "intake").iterdir()) == []


def test_rejected_fake_lands_in_rejected_folder(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    fake_reject(monkeypatch, reason="Spektrum-Cutoff bei 16 kHz")

    result = process_uploaded_audio(io.BytesIO(b"RIFF"), "fake.wav", cfg)

    assert result.ok is False
    assert result.status == "rejected"
    assert result.message == "Spektrum-Cutoff bei 16 kHz"
    assert result.dest_path == str(tmp_path / "inbox" / "_rejected" / "fake.wav")
    assert (tmp_path / "inbox" / "_rejected" / "fake.wav").exists()


def test_clipped_upload_reports_brickwall(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    fake_reject(monkeypatch, clipped=True)

    result = process_uploaded_audio(io.BytesIO(b"RIFF"), "laut.wav", cfg)

    assert result.status == "rejected"
    assert result.message == "Brickwall-Master (Clipping)"
    assert result.dest_path == str(tmp_path / "inbox" / "_rejected" / "clipped" / "laut.wav")


def test_duplicate_upload_reports_collection(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)

    def _fake(t, path, inbox, cfg_):
        folder = inbox / "_rejected" / "duplicate"
        folder.mkdir(parents=True, exist_ok=True)
        path.replace(folder / path.name)
        t.duplicate_of = "/music/Schranz/8A/dup.wav"
        return None

    monkeypatch.setattr("sc_digger.main.finish_file", _fake)

    result = process_uploaded_audio(io.BytesIO(b"RIFF"), "dup.wav", cfg)

    assert result.ok is False
    assert result.status == "rejected"
    assert "Sammlung" in result.message
    assert result.dest_path == str(tmp_path / "inbox" / "_rejected" / "duplicate" / "dup.wav")
    assert list((tmp_path / "intake").iterdir()) == []


def test_processing_error_is_reported_and_upload_removed(tmp_path, monkeypatch):
    """Wirft finish_file, ist der Upload kein Rest im Eingang, sondern eine Fehlermeldung."""
    cfg = make_cfg(tmp_path)

    def _fake(t, path, inbox, cfg_):
        raise RuntimeError("librosa kaputt")

    monkeypatch.setattr("sc_digger.main.finish_file", _fake)

    result = process_uploaded_audio(io.BytesIO(b"RIFF"), "kaputt.wav", cfg)

    assert result.ok is False
    assert result.status == "error"
    assert "Verarbeitung fehlgeschlagen" in result.message
    assert "librosa kaputt" in result.message
    assert list((tmp_path / "intake").iterdir()) == []


def test_failed_check_moves_file_to_fehler(tmp_path, monkeypatch):
    """finish_file gibt None, die Datei liegt noch: Prüfung scheiterte -> _fehler."""
    cfg = make_cfg(tmp_path)

    def _fake(t, path, inbox, cfg_):
        t.notes.append("ffprobe nicht verfügbar")
        return None

    monkeypatch.setattr("sc_digger.main.finish_file", _fake)

    result = process_uploaded_audio(io.BytesIO(b"RIFF"), "unkenntlich.wav", cfg)

    assert result.ok is False
    assert result.status == "error"
    assert result.message == "ffprobe nicht verfügbar"
    assert result.dest_path == str(tmp_path / "intake" / "_fehler" / "unkenntlich.wav")
    assert (tmp_path / "intake" / "_fehler" / "unkenntlich.wav").exists()
    assert list((tmp_path / "intake").glob("*.wav")) == []


def test_existing_intake_file_is_never_overwritten(tmp_path, monkeypatch):
    """Gleicher Dateiname im Eingang: der Upload bekommt einen neuen Namen (Regel 4)."""
    cfg = make_cfg(tmp_path)
    intake = tmp_path / "intake"
    intake.mkdir()
    (intake / "track.wav").write_bytes(b"alt")
    fake_success(monkeypatch)

    result = process_uploaded_audio(io.BytesIO(b"neu"), "track.wav", cfg)

    assert (intake / "track.wav").read_bytes() == b"alt"
    assert result.ok is True
    assert result.dest_path == str(tmp_path / "inbox" / "track_1.wav")


def test_read_error_is_caught_and_leaves_no_file(tmp_path):
    cfg = make_cfg(tmp_path)

    result = process_uploaded_audio(_Boom(), "halb.wav", cfg)

    assert result.ok is False
    assert result.status == "error"
    assert list((tmp_path / "intake").iterdir()) == []


def test_unconfigured_intake_dir_is_reported(tmp_path):
    cfg = make_cfg(tmp_path)
    del cfg.raw["download"]["intake_dir"]

    result = process_uploaded_audio(io.BytesIO(b"RIFF"), "track.wav", cfg)

    assert result.ok is False
    assert result.status == "error"
    assert "intake_dir" in result.message


def test_unconfigured_inbox_is_reported(tmp_path):
    cfg = make_cfg(tmp_path)
    del cfg.raw["download"]["inbox_dir"]

    result = process_uploaded_audio(io.BytesIO(b"RIFF"), "track.wav", cfg)

    assert result.ok is False
    assert result.status == "error"
    assert "inbox_dir" in result.message
    assert not (tmp_path / "intake").exists()


# ---------------------------------------------------------- POST /api/upload-intake
def test_api_upload_requires_auth(tmp_path):
    client = client_for(make_cfg(tmp_path))

    r = client.post("/api/upload-intake", files={"file": ("t.wav", b"RIFF", "audio/wav")},
                    headers=ORIGIN)

    assert r.status_code == 401


def test_api_upload_rejects_missing_origin(tmp_path):
    """Schreibende Route: ohne Origin/Referer kein Upload (CSRF-Schutz)."""
    client = client_for(make_cfg(tmp_path))

    r = client.post("/api/upload-intake", files={"file": ("t.wav", b"RIFF", "audio/wav")},
                    auth=("sc", PASSWORD))

    assert r.status_code == 403
    assert not (tmp_path / "intake").exists()


def test_api_upload_rejects_foreign_origin(tmp_path):
    client = client_for(make_cfg(tmp_path))

    r = client.post("/api/upload-intake", files={"file": ("t.wav", b"RIFF", "audio/wav")},
                    auth=("sc", PASSWORD), headers={"origin": "https://boese.example"})

    assert r.status_code == 403


def test_api_upload_without_file_field_is_bad_request(tmp_path):
    client = client_for(make_cfg(tmp_path))

    r = client.post("/api/upload-intake", files={"andere": ("t.wav", b"RIFF", "audio/wav")},
                    auth=("sc", PASSWORD), headers=ORIGIN)

    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_api_upload_non_multipart_body_is_bad_request(tmp_path):
    client = client_for(make_cfg(tmp_path))

    r = client.post("/api/upload-intake", content=b"file=track.wav",
                    auth=("sc", PASSWORD),
                    headers={**ORIGIN, "content-type": "application/x-www-form-urlencoded"})

    assert r.status_code == 400


def test_api_upload_non_audio_is_unprocessable(tmp_path):
    client = client_for(make_cfg(tmp_path))

    r = client.post("/api/upload-intake",
                    files={"file": ("notizen.txt", b"kein audio", "text/plain")},
                    auth=("sc", PASSWORD), headers=ORIGIN)

    assert r.status_code == 422
    body = r.json()
    assert body["ok"] is False
    assert body["status"] == "error"
    assert "Audio" in body["message"]
    assert not (tmp_path / "intake").exists()


def test_api_upload_success_returns_upload_result(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    fake_success(monkeypatch, bpm=150.0, key="11A")
    client = client_for(cfg)

    r = client.post("/api/upload-intake",
                    files={"file": ("Svetec - Acid Kick.wav", b"RIFF", "audio/wav")},
                    auth=("sc", PASSWORD), headers=ORIGIN)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "inbox"
    assert body["filename"] == "Svetec - Acid Kick.wav"
    assert body["dest_path"] == str(tmp_path / "inbox" / "Svetec - Acid Kick.wav")
    assert body["bpm"] == 150.0
    assert body["key_camelot"] == "11A"
    assert (tmp_path / "inbox" / "Svetec - Acid Kick.wav").read_bytes() == b"RIFF"


def test_api_upload_rejected_is_unprocessable(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    fake_reject(monkeypatch, reason="MP3 mit nur 128 kbps")
    client = client_for(cfg)

    r = client.post("/api/upload-intake", files={"file": ("fake.wav", b"RIFF", "audio/wav")},
                    auth=("sc", PASSWORD), headers=ORIGIN)

    assert r.status_code == 422
    body = r.json()
    assert body["ok"] is False
    assert body["status"] == "rejected"
    assert body["message"] == "MP3 mit nur 128 kbps"


def test_api_upload_without_intake_dir_is_unprocessable(tmp_path):
    cfg = make_cfg(tmp_path)
    del cfg.raw["download"]["intake_dir"]
    client = client_for(cfg)

    r = client.post("/api/upload-intake", files={"file": ("track.wav", b"RIFF", "audio/wav")},
                    auth=("sc", PASSWORD), headers=ORIGIN)

    assert r.status_code == 422
    assert r.json()["status"] == "error"


# ------------------------------------------------------------------ Uploadzone im HTML
def test_digest_page_has_drag_and_drop_zone(tmp_path):
    client = client_for(make_cfg(tmp_path))

    r = client.get("/digest", auth=("sc", PASSWORD))

    assert r.status_code == 200
    assert 'id="upload-zone"' in r.text
    assert 'id="upload-input"' in r.text
    assert 'id="upload-status"' in r.text
    # Statusrückmeldung ohne Seiten-Reload
    assert 'role="status"' in r.text
    assert "/api/upload-intake" in r.text
    # Drag-&-Drop-Ereignisse und Dateiauswahl sind verdrahtet
    for event in ("dragenter", "dragover", "dragleave", "drop"):
        assert event in r.text
    assert ".upload-zone" in r.text
    assert 'accept=".wav,.aiff,.aif,.flac,.mp3,.m4a,audio/*"' in r.text
