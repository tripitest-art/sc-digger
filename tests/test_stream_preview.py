"""Eigene Tests für die Stream-Preview (Teil 3 von #53, Issue #168).

Ergänzt die Akzeptanztests um Randfälle: exakter ffmpeg-Aufruf inkl. -ss,
client_id-Anhang in der zweiten _get-Stufe, HLS-Fallback, leeres media,
Aufräumen der Temp-Datei in allen Pfaden von preview_stream_reply und
Fehlertexte ohne Secrets. Alle Tests laufen ohne Netz und ohne echtes ffmpeg.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from sc_digger import bot, preview
from sc_digger.models import Config
from sc_digger.preview import extract_preview_from_url
from sc_digger.soundcloud import SoundCloudClient


def _track(transcodings=None):
    return {"id": 1, "media": {"transcodings": transcodings or []}}


def _tc(url, protocol):
    return {"url": url, "format": {"protocol": protocol}}


def test_transcoding_url_empty_media_list_is_none():
    assert SoundCloudClient._transcoding_url({"media": {}}) is None
    assert SoundCloudClient._transcoding_url({"media": None}) is None


def test_transcoding_url_skips_entries_without_url():
    raw = _track([{"format": {"protocol": "progressive"}}, _tc("https://e/hls", "hls")])
    assert SoundCloudClient._transcoding_url(raw) == "https://e/hls"


def test_preview_url_second_stage_appends_client_id(monkeypatch):
    """_get hängt client_id selbst an; preview_url darf params nicht überschreiben."""
    sc = SoundCloudClient()
    sc.client_id = "cid" * 1
    raw = _track([_tc("https://e/prog", "progressive")])
    calls = []
    results = iter([raw, {"url": "https://e/stream.mp3"}])

    def fake_get(path_or_url, params=None):
        calls.append((path_or_url, params))
        return next(results)

    monkeypatch.setattr(sc, "_get", fake_get)
    assert sc.preview_url(42) == "https://e/stream.mp3"
    assert calls[0][0] == "/tracks/42"
    assert calls[1][0] == "https://e/prog"


def test_preview_url_none_when_first_stage_has_no_transcoding(monkeypatch):
    sc = SoundCloudClient()
    monkeypatch.setattr(sc, "_get", lambda path_or_url, params=None: {"id": 1})
    assert sc.preview_url(7) is None


def test_extract_preview_from_url_uses_ss_and_bitrate(monkeypatch, tmp_path):
    out = tmp_path / "sub" / "preview.ogg"
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"OggS-fake")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("sc_digger.preview.subprocess.run", fake_run)
    assert extract_preview_from_url(
        "https://e/prog", out, start_s=3.5, duration_s=20.0, bitrate_kbps=64
    ) is True
    cmd = seen["cmd"]
    assert cmd[cmd.index("-ss") + 1] == "3.500"
    assert cmd[cmd.index("-t") + 1] == "20"
    assert cmd[cmd.index("-b:a") + 1] == "64k"
    assert cmd[-1] == str(out)


def test_extract_preview_from_url_missing_ffmpeg_returns_false(monkeypatch, tmp_path):
    out = tmp_path / "preview.ogg"

    def boom(cmd, **kw):
        raise FileNotFoundError("ffmpeg missing")

    monkeypatch.setattr("sc_digger.preview.subprocess.run", boom)
    assert extract_preview_from_url("https://e/prog", out) is False
    assert not out.exists()


def test_extract_preview_from_url_empty_output_cleans(monkeypatch, tmp_path):
    out = tmp_path / "preview.ogg"

    def fake_run(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("sc_digger.preview.subprocess.run", fake_run)
    assert extract_preview_from_url("https://e/prog", out) is False
    assert not out.exists()


def _stream_cfg(enabled=True):
    return Config({"preview": {"stream_enabled": enabled}})


def test_preview_stream_reply_cleans_temp_on_ffmpeg_failure(monkeypatch):
    cfg = _stream_cfg()

    class FakeSC:
        def preview_url(self, sc_id):
            return "https://e/prog"

    created = []

    def fake_extract(url, out, **kw):
        out.write_bytes(b"partial")
        created.append(out)
        return False

    monkeypatch.setattr(bot, "SoundCloudClient", lambda: FakeSC())
    monkeypatch.setattr(bot, "extract_preview_from_url", fake_extract)
    monkeypatch.setattr(bot, "send_telegram_voice", lambda *a, **k: (_ for _ in ()).throw(AssertionError("kein Versand")))
    result = bot.preview_stream_reply(cfg, 9)
    assert isinstance(result, str)
    assert created and not created[0].exists()


def test_preview_stream_reply_cleans_temp_on_telegram_error(monkeypatch):
    from sc_digger.output import TelegramError

    cfg = _stream_cfg()
    created = []

    class FakeSC:
        def preview_url(self, sc_id):
            return "https://e/prog"

    def fake_extract(url, out, **kw):
        out.write_bytes(b"OggS-fake")
        created.append(out)
        return True

    def boom_send(*a, **k):
        raise TelegramError("kaputt")

    monkeypatch.setattr(bot, "SoundCloudClient", lambda: FakeSC())
    monkeypatch.setattr(bot, "extract_preview_from_url", fake_extract)
    monkeypatch.setattr(bot, "send_telegram_voice", boom_send)
    result = bot.preview_stream_reply(cfg, 10)
    assert isinstance(result, str)
    assert "kaputt" in result
    assert created and not created[0].exists()


def test_preview_stream_reply_no_secret_in_error_text(monkeypatch):
    cfg = _stream_cfg()

    class FakeSC:
        def preview_url(self, sc_id):
            raise RuntimeError("secret-token-abc")

    monkeypatch.setattr(bot, "SoundCloudClient", lambda: FakeSC())
    result = bot.preview_stream_reply(cfg, 11)
    assert isinstance(result, str)
    assert "secret-token-abc" not in result


def test_preview_reply_sc_digits_zero(monkeypatch):
    """sc:0 ist formal gültig und geht an preview_stream_reply (nicht Usage)."""
    cfg = Config({"preview": {"stream_enabled": True}, "state": {"track_db_path": ""}})
    seen = {}
    monkeypatch.setattr(bot, "preview_stream_reply", lambda cfg, sc_id: seen.setdefault("id", sc_id))
    bot.preview_reply(cfg, "/preview sc:0", chat_id="c")
    assert seen == {"id": 0}
