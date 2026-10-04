"""Akzeptanztests Teil 3 von #53: Stream-Preview für nicht heruntergeladene Tracks.

Alle Tests laufen ohne Netz und ohne echtes ffmpeg/Telegram. Sie sind heute rot,
weil weder SoundCloudClient.preview_url/_transcoding_url noch
preview.extract_preview_from_url noch bot.preview_stream_reply existieren und
preview_reply die sc:-Präfix-Route nicht kennt.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from sc_digger import bot
from sc_digger.models import Config
from sc_digger.preview import extract_preview_from_url
from sc_digger.soundcloud import SoundCloudClient, SoundCloudError


def _track(transcodings=None):
    return {
        "id": 987654321,
        "kind": "track",
        "title": "Stream-Test",
        "permalink_url": "https://soundcloud.com/a/stream-test",
        "user": {"username": "a", "permalink_url": "https://soundcloud.com/a"},
        "media": {"transcodings": transcodings or []},
    }


def _tc(url, protocol):
    return {"url": url, "format": {"protocol": protocol}}


def test_transcoding_url_prefers_progressive():
    raw = _track([_tc("https://e/hls", "hls"), _tc("https://e/prog", "progressive")])
    assert SoundCloudClient._transcoding_url(raw) == "https://e/prog"


def test_transcoding_url_falls_back_to_hls():
    raw = _track([_tc("https://e/hls", "hls")])
    assert SoundCloudClient._transcoding_url(raw) == "https://e/hls"


def test_transcoding_url_none_without_useful_media():
    assert SoundCloudClient._transcoding_url({"id": 1}) is None
    assert SoundCloudClient._transcoding_url({}) is None
    assert SoundCloudClient._transcoding_url(_track([])) is None


def test_preview_url_uses_get_and_returns_stream_url(monkeypatch):
    sc = SoundCloudClient()
    raw = _track([_tc("https://e/prog", "progressive")])
    calls = []
    results = iter([raw, {"url": "https://e/stream.mp3"}])
    monkeypatch.setattr(
        sc,
        "_get",
        lambda path_or_url, params=None: (calls.append((path_or_url, params)) or next(results)),
    )
    url = sc.preview_url(987654321)
    assert len(calls) == 2, "preview_url muss beide _get-Stufen durchlaufen"
    assert url == "https://e/stream.mp3"


def test_preview_url_none_when_second_stage_has_no_url(monkeypatch):
    sc = SoundCloudClient()
    raw = _track([_tc("https://e/prog", "progressive")])
    results = iter([raw, {}])
    monkeypatch.setattr(sc, "_get", lambda path_or_url, params=None: next(results))
    assert sc.preview_url(987654321) is None


def test_preview_url_propagates_soundcloud_error(monkeypatch):
    sc = SoundCloudClient()

    def boom(path_or_url, params=None):
        raise SoundCloudError("kaputt")

    monkeypatch.setattr(sc, "_get", boom)
    with pytest.raises(SoundCloudError):
        sc.preview_url(1)


def test_extract_preview_from_url_ok(monkeypatch, tmp_path):
    out = tmp_path / "preview.ogg"
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"OggS-fake")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("sc_digger.preview.subprocess.run", fake_run)
    ok = extract_preview_from_url("https://e/prog", out, start_s=0.0, duration_s=20.0, bitrate_kbps=64)
    assert ok is True
    assert seen["cmd"][-1] == str(out)
    idx = seen["cmd"].index("-t")
    assert seen["cmd"][idx + 1] == "20"


def test_extract_preview_from_url_failure_returns_false_and_cleans(monkeypatch, tmp_path):
    out = tmp_path / "preview.ogg"
    out.write_bytes(b"partial")
    monkeypatch.setattr(
        "sc_digger.preview.subprocess.run",
        lambda cmd, **kw: SimpleNamespace(returncode=1),
    )
    assert extract_preview_from_url("https://e/prog", out) is False
    assert not out.exists()


def test_preview_stream_reply_disabled_by_config(monkeypatch):
    cfg = Config({"preview": {"stream_enabled": False}})

    def denied(*a, **k):
        raise AssertionError("darf bei deaktiviertem Stream-Preview nicht laufen")

    monkeypatch.setattr(bot, "SoundCloudClient", denied)
    monkeypatch.setattr(bot, "extract_preview_from_url", denied)
    monkeypatch.setattr(bot, "send_telegram_voice", denied)
    assert isinstance(bot.preview_stream_reply(cfg, 111), str)


def test_preview_stream_reply_no_stream_url(monkeypatch):
    cfg = Config({"preview": {"stream_enabled": True}})

    class FakeSC:
        def preview_url(self, sc_id):
            return None

    monkeypatch.setattr(bot, "SoundCloudClient", lambda: FakeSC())
    monkeypatch.setattr(bot, "send_telegram_voice", lambda *a, **k: (_ for _ in ()).throw(AssertionError("keine Voice ohne URL")))
    result = bot.preview_stream_reply(cfg, 222)
    assert isinstance(result, str)
    assert "kein Stream-Preview" in result


def test_preview_stream_reply_success_sends_voice(monkeypatch):
    cfg = Config({"preview": {"stream_enabled": True}})

    class FakeSC:
        def __init__(self):
            self.seen = None

        def preview_url(self, sc_id):
            self.seen = sc_id
            return "https://e/prog"

    sc = FakeSC()
    sent = []

    def fake_extract(url, out, **kw):
        out.write_bytes(b"OggS-fake")
        return True

    monkeypatch.setattr(bot, "SoundCloudClient", lambda: sc)
    monkeypatch.setattr(bot, "extract_preview_from_url", fake_extract)
    monkeypatch.setattr(bot, "send_telegram_voice", lambda *a, **k: sent.append(a))
    assert bot.preview_stream_reply(cfg, 333) is None
    assert sc.seen == 333
    assert len(sent) == 1


def test_preview_reply_sc_prefix_delegates(monkeypatch):
    cfg = Config({"preview": {"stream_enabled": True}, "state": {"track_db_path": ""}})
    seen = {}

    def fake_stream(cfg, sc_id):
        seen["sc_id"] = sc_id
        return None

    monkeypatch.setattr(bot, "preview_stream_reply", fake_stream)
    assert bot.preview_reply(cfg, "/preview sc:123", chat_id="c") is None
    assert seen == {"sc_id": 123}


def test_preview_reply_digits_without_prefix_stays_local(monkeypatch, tmp_path):
    cfg = Config({"state": {"track_db_path": str(tmp_path / "t.sqlite")}})
    called = []
    monkeypatch.setattr(bot, "preview_stream_reply", lambda *a, **k: called.append(1))
    reply = bot.preview_reply(cfg, "/preview 123", chat_id="c")
    assert called == []
    assert "Kein Track mit id 123 in der Track-DB." in reply


def test_preview_reply_sc_without_number_returns_usage(monkeypatch):
    cfg = Config({"state": {"track_db_path": ""}})
    monkeypatch.setattr(bot, "preview_stream_reply", lambda *a, **k: (_ for _ in ()).throw(AssertionError("darf nicht laufen")))
    reply = bot.preview_reply(cfg, "/preview sc:", chat_id="c")
    assert reply == bot.PREVIEW_USAGE
    assert "sc:<" in bot.PREVIEW_USAGE
