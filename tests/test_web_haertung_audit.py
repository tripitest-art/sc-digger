"""Sicherheits-Audit Web 2026-10-29, Befunde 5 bis 8 (PR #416).

5: ``SoundCloudClient._unshorten`` ruft nur https-Links mit exaktem Host ``on.soundcloud.com``
   ab und folgt Weiterleitungen nur auf https-soundcloud.com-Ziele.
6: Kein roher Ausnahmetext (Pfade, ffmpeg-Ausgabe) mehr im Browser; Log und Notizen geschwärzt.
7: Upload und URL-Scanner laufen im Threadpool statt auf der Event-Loop.
8: Upload-Größenlimit (Header und Zählen beim Kopieren, 413) und ``-protocol_whitelist``.
"""
from __future__ import annotations

import io
import logging
import subprocess
import threading
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import sc_digger.web.app as app_module
import sc_digger.web.intake as intake_module
from sc_digger import analysis, preview, quality, stems, structure
from sc_digger.models import Config
from sc_digger.soundcloud import SoundCloudClient
from sc_digger.web.app import create_app
from sc_digger.web.intake import process_uploaded_audio

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-audit"
ORIGIN = {"origin": "http://testserver"}


def make_cfg(tmp_path: Path) -> Config:
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


def client_for(tmp_path: Path) -> TestClient:
    return TestClient(create_app(make_cfg(tmp_path), password=PASSWORD))


# ------------------------------------------------------------------ Befund 5: _unshorten
class _Resp:
    def __init__(self, url: str, status: int = 200, location: str | None = None):
        self.url = url
        self.status_code = status
        self.headers = {"Location": location} if location else {}


class _FakeSession:
    """Antwortet je URL aus einer Tabelle und merkt sich jede abgerufene URL."""

    def __init__(self, table: dict[str, _Resp]):
        self.table = table
        self.calls: list[str] = []

    def get(self, url, **kw):
        assert kw.get("allow_redirects") is False  # Weiterleitungen prüft _unshorten selbst
        self.calls.append(url)
        return self.table.get(url, _Resp(url))


def client_with_session(table) -> tuple[SoundCloudClient, _FakeSession]:
    sc = SoundCloudClient()
    sess = _FakeSession(table)
    sc.s = sess
    return sc, sess


@pytest.mark.parametrize("url", [
    "http://192.168.1.5:8123/api/x?on.soundcloud.com",       # Substring im Query
    "https://evil.example/on.soundcloud.com",                # Substring im Pfad
    "https://on.soundcloud.com.evil.example/abc",            # Host beginnt mit dem Kurzlink-Host
    "https://evil.example/?next=https://on.soundcloud.com/a",
    "http://on.soundcloud.com/abc",                          # richtiger Host, aber kein https
    "ftp://on.soundcloud.com/abc",
])
def test_unshorten_does_not_fetch_foreign_or_non_https_urls(url):
    sc, sess = client_with_session({})
    assert sc._unshorten(url) == url
    assert sess.calls == []


def test_unshorten_resolves_https_shortlink_to_soundcloud():
    short = "https://on.soundcloud.com/abc123"
    final = "https://soundcloud.com/svetec/acid-kick"
    sc, sess = client_with_session({short: _Resp(short, 302, location=final)})
    assert sc._unshorten(short) == final
    assert sess.calls == [short, final]


def test_unshorten_host_compare_is_case_insensitive():
    short = "https://ON.SoundCloud.com/abc123"
    sc, sess = client_with_session({})
    assert sc._unshorten(short) == short
    assert sess.calls == [short]


@pytest.mark.parametrize("target", [
    "http://soundcloud.com/svetec/x",                  # Weiterleitung auf http
    "https://evil.example/landing",                    # fremder Host
    "https://soundcloud.com.evil.example/x",           # Host nur mit soundcloud.com-Präfix
    "http://192.168.1.5:8123/api/x",                   # Heimnetz
])
def test_unshorten_does_not_follow_redirect_to_foreign_or_http_target(target):
    short = "https://on.soundcloud.com/abc123"
    sc, sess = client_with_session({short: _Resp(short, 302, location=target)})
    assert sc._unshorten(short) == short
    assert sess.calls == [short]  # das Ziel wurde nie angefragt


def test_unshorten_stops_on_redirect_loop():
    short = "https://on.soundcloud.com/abc123"
    sc, sess = client_with_session({short: _Resp(short, 302, location=short)})
    assert sc._unshorten(short) == short
    assert len(sess.calls) <= 10


# ------------------------------------------------------------------ Befund 6: Ausnahmetexte
SECRET_PATH = "/music/inbox/geheim/pfad"


def test_upload_exception_text_not_in_result_but_redacted_in_log(tmp_path, monkeypatch, caplog):
    cfg = make_cfg(tmp_path)

    def boom(src, dst, max_bytes=0):
        raise OSError(f"[Errno 13] Permission denied: '{SECRET_PATH}'")

    monkeypatch.setattr(intake_module, "_copy_stream", boom)
    with caplog.at_level(logging.WARNING):
        result = process_uploaded_audio(io.BytesIO(b"RIFF"), "a.wav", cfg)

    assert result.status == "error"
    assert SECRET_PATH not in result.message
    assert "Permission denied" not in result.message
    assert "Details im Log" in result.message
    assert "Permission denied" in caplog.text  # die Ursache bleibt im Log sichtbar


def test_upload_processing_error_log_is_redacted(tmp_path, monkeypatch, caplog):
    cfg = make_cfg(tmp_path)
    monkeypatch.setenv("SOUNDCLOUD_AUTH_TOKEN", "tok-12345678-geheim")

    def fake(t, path, inbox, cfg_):
        raise RuntimeError("scdl --auth-token tok-12345678-geheim kaputt")

    monkeypatch.setattr("sc_digger.main.finish_file", fake)
    with caplog.at_level(logging.WARNING):
        result = process_uploaded_audio(io.BytesIO(b"RIFF"), "b.wav", cfg)

    assert "tok-12345678-geheim" not in result.message
    assert "tok-12345678-geheim" not in caplog.text
    assert "kaputt" in caplog.text


def test_upload_failed_check_note_not_returned_to_browser(tmp_path, monkeypatch, caplog):
    cfg = make_cfg(tmp_path)

    def fake(t, path, inbox, cfg_):
        t.notes.append(f"Audio-Analyse fehlgeschlagen: [Errno 2] No such file: '{SECRET_PATH}/x.wav'")
        return None

    monkeypatch.setattr("sc_digger.main.finish_file", fake)
    with caplog.at_level(logging.WARNING):
        result = process_uploaded_audio(io.BytesIO(b"RIFF"), "c.wav", cfg)

    assert result.status == "error"
    assert SECRET_PATH not in result.message
    assert "Audio-Analyse fehlgeschlagen" in caplog.text


@pytest.mark.parametrize("exc", [
    RuntimeError(f"Request fehlgeschlagen: https://api-v2.soundcloud.com/x?client_id=abc&path={SECRET_PATH}"),
    app_module.SoundCloudError(f"Request fehlgeschlagen: {SECRET_PATH}"),
])
def test_check_url_error_text_not_sent_to_browser(tmp_path, monkeypatch, exc):
    def boom(cfg, url):
        raise exc

    monkeypatch.setattr(app_module, "_scan_url", boom)
    r = client_for(tmp_path).post(
        "/api/check-url", json={"url": "https://soundcloud.com/x"},
        auth=("sc", PASSWORD), headers=ORIGIN,
    )
    assert r.status_code in (422, 502)
    assert r.json()["ok"] is False
    assert SECRET_PATH not in r.text
    assert "client_id" not in r.text


def test_check_url_wrong_kind_message_still_reaches_browser(tmp_path, monkeypatch):
    class FakeSC:
        def __init__(self, *a, **k):
            pass

        def resolve_kind(self, url):
            return "user"

    monkeypatch.setattr(app_module, "SoundCloudClient", FakeSC)
    r = client_for(tmp_path).post(
        "/api/check-url", json={"url": "https://soundcloud.com/x"},
        auth=("sc", PASSWORD), headers=ORIGIN,
    )
    assert r.status_code == 422
    assert "weder ein Track- noch ein Playlist-Link" in r.json()["error"]


# ------------------------------------------------------------------ Befund 7: Threadpool
def test_upload_runs_off_the_event_loop(tmp_path, monkeypatch):
    seen: dict[str, bool] = {}

    def fake(file_obj, filename, cfg):
        # In der Event-Loop-Thread läuft ein laufender asyncio-Loop; im Threadpool nicht.
        import asyncio
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        seen["thread"] = threading.current_thread().name
        return intake_module.UploadResult(ok=True, filename=filename, status="inbox", message="ok")

    monkeypatch.setattr(app_module, "process_uploaded_audio", fake)
    r = client_for(tmp_path).post(
        "/api/upload-intake", files={"file": ("t.wav", b"RIFF", "audio/wav")},
        auth=("sc", PASSWORD), headers=ORIGIN,
    )
    assert r.status_code == 200
    assert seen["on_loop"] is False


def test_check_url_runs_off_the_event_loop(tmp_path, monkeypatch):
    seen: dict[str, bool] = {}

    def fake(cfg, url):
        import asyncio
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        return {"kind": "track", "source": "x", "total": 0, "tracks": []}

    monkeypatch.setattr(app_module, "_scan_url", fake)
    r = client_for(tmp_path).post(
        "/api/check-url", json={"url": "https://soundcloud.com/x"},
        auth=("sc", PASSWORD), headers=ORIGIN,
    )
    assert r.status_code == 200
    assert seen["on_loop"] is False


# ------------------------------------------------------------------ Befund 8: Größenlimit
def test_copy_stream_aborts_above_limit():
    dst = io.BytesIO()
    with pytest.raises(intake_module.UploadTooLarge):
        intake_module._copy_stream(io.BytesIO(b"x" * 100), dst, 99)
    dst2 = io.BytesIO()
    intake_module._copy_stream(io.BytesIO(b"x" * 100), dst2, 100)  # genau am Limit ist erlaubt
    assert len(dst2.getvalue()) == 100


def test_oversize_upload_is_rejected_and_leaves_no_file(tmp_path):
    cfg = make_cfg(tmp_path)
    result = process_uploaded_audio(io.BytesIO(b"x" * 2048), "gross.wav", cfg, max_bytes=1024)
    assert result.ok is False
    assert result.status == "too_large"
    intake = tmp_path / "intake"
    assert [p.name for p in intake.iterdir()] == []  # weder Zieldatei noch .part


def test_route_returns_413_for_oversize_body_while_copying(tmp_path, monkeypatch):
    monkeypatch.setattr(intake_module, "MAX_UPLOAD_BYTES", 1024)
    r = client_for(tmp_path).post(
        "/api/upload-intake", files={"file": ("gross.wav", b"x" * 4096, "audio/wav")},
        auth=("sc", PASSWORD), headers=ORIGIN,
    )
    assert r.status_code == 413
    assert r.json()["status"] == "too_large"


def test_route_rejects_oversize_content_length_before_reading_body(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(app_module, "process_uploaded_audio", lambda *a, **k: called.append(1))
    r = client_for(tmp_path).post(
        "/api/upload-intake", content=b"x",
        auth=("sc", PASSWORD),
        headers={**ORIGIN, "content-type": "multipart/form-data; boundary=x",
                 "content-length": str(app_module.MAX_UPLOAD_BYTES + 10 * 1024 * 1024)},
    )
    assert r.status_code == 413
    assert called == []


def test_route_rejects_garbage_content_length(tmp_path):
    r = client_for(tmp_path).post(
        "/api/upload-intake", content=b"x", auth=("sc", PASSWORD),
        headers={**ORIGIN, "content-type": "multipart/form-data; boundary=x",
                 "content-length": "abc"},
    )
    assert r.status_code in (400, 413)


# ------------------------------------------------------------------ Befund 8: protocol_whitelist
def _capture(monkeypatch, module):
    cmds: list[list[str]] = []

    def fake_run(cmd, *a, **k):
        cmds.append([str(c) for c in cmd])
        return subprocess.CompletedProcess(cmd, 1, b"", b"")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    return cmds


def _assert_whitelist_before_input(cmd: list[str]):
    assert "-protocol_whitelist" in cmd, cmd
    i = cmd.index("-protocol_whitelist")
    assert cmd[i + 1] == "file,pipe"
    # Eingabe-Option: wirkt nur vor der Eingabe (ffmpeg: -i, ffprobe: Pfad als letztes Argument).
    assert i < (cmd.index("-i") if "-i" in cmd else len(cmd) - 1), cmd


def test_ffmpeg_and_ffprobe_calls_have_protocol_whitelist(tmp_path, monkeypatch):
    p = tmp_path / "a.wav"
    p.write_bytes(b"RIFF")
    calls = (
        (quality, lambda: quality.probe(p)),
        (quality, lambda: quality.is_32bit_or_float(p)),
        (quality, lambda: quality.convert_to_pcm24(p)),
        (quality, lambda: quality.convert_wav_to_flac(p)),
        (quality, lambda: quality._decode_window(p, 5, None)),
        (quality, lambda: quality._decode_window(p, 5, 10)),
        (quality, lambda: quality.loudness_report(p)),
        (analysis, lambda: analysis._decode_audio(p, 22050, 60, 30)),
        (structure, lambda: structure._decode_audio(p)),
        (stems, lambda: stems._decode_audio(p)),
        (preview, lambda: preview._decode_mono(p, 22050)),
        (preview, lambda: preview._has_audio_stream(p)),
        (preview, lambda: preview.extract_preview(p, tmp_path / "o.ogg", start_s=0.0)),
    )
    for module, call in calls:
        cmds = _capture(monkeypatch, module)
        try:
            call()
        except Exception:
            pass
        assert cmds, "kein ffmpeg-/ffprobe-Aufruf erfasst"
        for cmd in cmds:
            _assert_whitelist_before_input(cmd)


def test_whitelisted_ffmpeg_still_decodes_a_real_wav(tmp_path):
    """Die Option muss von der echten ffmpeg-Version akzeptiert werden (Rauchtest, kein Fake)."""
    import shutil
    import wave

    if shutil.which("ffmpeg") is None:  # Umgebung ohne ffmpeg: Rauchtest nicht möglich
        pytest.fail("ffmpeg fehlt im PATH (AGENTS.md: Tests brauchen ffmpeg)")
    p = tmp_path / "s.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes((np.sin(np.linspace(0, 2000, 44100 * 2)) * 8000).astype("<i2").tobytes())
    assert quality.probe(p)["streams"][0]["codec_name"].startswith("pcm_")
    assert quality._decode_window(p, 1, None).size > 0

