"""Akzeptanztests: SoundCloud-Login vor dem Lauf prüfen."""
from __future__ import annotations

import logging

from sc_digger import main as m
from sc_digger import soundcloud
from sc_digger.health import Health
from sc_digger.models import Config
from sc_digger.soundcloud import SoundCloudClient, TokenStatus, check_auth_token

from tests.test_modes import CFG, mk

TOKEN = "geheim-1234567890abcdef"


class Resp:
    def __init__(self, status_code: int):
        self.status_code = status_code


class Get:
    def __init__(self, status: int = 200, error: Exception | None = None):
        self.status, self.error, self.calls = status, error, []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return Resp(self.status)


class FakeSC:
    def __init__(self):
        self.asked = 0

    def get_client_id(self) -> str:
        self.asked += 1
        return "cid"


class Check:
    def __init__(self, status: TokenStatus):
        self.status, self.calls = status, []

    def __call__(self, token, *, client_id=None):
        self.calls.append((token, client_id))
        return self.status


def _cfg(tmp_path):
    cfg = Config(dict(CFG.raw))
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path / "coll"),
                           "inbox_dir": str(tmp_path / "inbox")}
    return cfg


# --- check_auth_token ------------------------------------------------------------

def test_missing_token_needs_no_request():
    get = Get()
    assert check_auth_token(None, get=get) is TokenStatus.MISSING
    assert check_auth_token("", get=get) is TokenStatus.MISSING
    assert get.calls == []


def test_valid_token_calls_me_with_oauth_header_and_client_id():
    get = Get(200)
    assert check_auth_token(TOKEN, client_id="cid", get=get, timeout=3.0) is TokenStatus.VALID
    assert len(get.calls) == 1
    url, kwargs = get.calls[0]
    assert url == "https://api-v2.soundcloud.com/me"
    assert kwargs["headers"]["Authorization"] == f"OAuth {TOKEN}"
    assert kwargs["params"] == {"client_id": "cid"}
    assert kwargs["timeout"] == 3.0


def test_rejected_token_is_invalid():
    assert check_auth_token(TOKEN, get=Get(401)) is TokenStatus.INVALID
    assert check_auth_token(TOKEN, get=Get(403)) is TokenStatus.INVALID


def test_server_or_network_trouble_is_unknown_and_never_logs_the_token(caplog):
    rq = soundcloud.requests
    caplog.set_level(logging.DEBUG)
    assert check_auth_token(TOKEN, get=Get(500)) is TokenStatus.UNKNOWN
    assert check_auth_token(TOKEN, get=Get(429)) is TokenStatus.UNKNOWN
    err = rq.ConnectionError(f"weg: Authorization OAuth {TOKEN}")
    assert check_auth_token(TOKEN, get=Get(error=err)) is TokenStatus.UNKNOWN
    assert TOKEN not in caplog.text


def test_client_get_client_id_returns_the_cached_id():
    sc = SoundCloudClient(request_delay=0)
    sc.client_id = "abc"
    assert sc.get_client_id() == "abc"


# --- Config.without_auth_token ---------------------------------------------------

def test_without_auth_token_hides_the_env_token_but_keeps_the_original(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUNDCLOUD_AUTH_TOKEN", TOKEN)
    cfg = _cfg(tmp_path)
    off = cfg.without_auth_token()
    assert off.soundcloud_auth_token is None
    assert cfg.soundcloud_auth_token == TOKEN
    assert off["state"] is cfg["state"]


# --- guard_auth_token ------------------------------------------------------------

def test_guard_without_token_does_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("SOUNDCLOUD_AUTH_TOKEN", raising=False)
    cfg, sc, check = _cfg(tmp_path), FakeSC(), Check(TokenStatus.VALID)
    assert m.guard_auth_token(cfg, sc, no_telegram=True, check=check) is cfg
    assert check.calls == [] and sc.asked == 0


def test_guard_valid_token_keeps_the_config(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUNDCLOUD_AUTH_TOKEN", TOKEN)
    cfg, check = _cfg(tmp_path), Check(TokenStatus.VALID)
    out = m.guard_auth_token(cfg, FakeSC(), no_telegram=True, check=check)
    assert out.soundcloud_auth_token == TOKEN
    assert check.calls == [(TOKEN, "cid")]


def test_guard_invalid_token_disables_downloads_and_alarms_once_without_token(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUNDCLOUD_AUTH_TOKEN", TOKEN)
    cfg = _cfg(tmp_path)
    sent = []
    monkeypatch.setattr(m, "send_telegram", lambda cfg, msgs, *a, **k: sent.append(msgs))
    out = m.guard_auth_token(cfg, FakeSC(), no_telegram=False, check=Check(TokenStatus.INVALID))
    assert out.soundcloud_auth_token is None
    assert len(sent) == 1
    alarm = "\n".join(sent[0])
    assert "sc_login" in alarm and "set-secret.sh" in alarm
    assert TOKEN not in alarm
    # zweiter Lauf mit abgelaufenem Token: kein zweiter Alarm
    m.guard_auth_token(cfg, FakeSC(), no_telegram=False, check=Check(TokenStatus.INVALID))
    assert len(sent) == 1
    # Token wieder gültig: genau eine Entwarnung
    m.guard_auth_token(cfg, FakeSC(), no_telegram=False, check=Check(TokenStatus.VALID))
    assert len(sent) == 2
    assert "läuft wieder" in "\n".join(sent[1])


def test_guard_unknown_keeps_trying_and_writes_no_health_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUNDCLOUD_AUTH_TOKEN", TOKEN)
    cfg = _cfg(tmp_path)
    out = m.guard_auth_token(cfg, FakeSC(), no_telegram=True, check=Check(TokenStatus.UNKNOWN))
    assert out.soundcloud_auth_token == TOKEN
    with Health(tmp_path / "seen.sqlite") as health:
        assert health.evaluate("sc_login", 1) is None


def test_guard_treats_errors_like_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUNDCLOUD_AUTH_TOKEN", TOKEN)

    def broken(token, *, client_id=None):
        raise RuntimeError("kaputt")

    out = m.guard_auth_token(_cfg(tmp_path), FakeSC(), no_telegram=True, check=broken)
    assert out.soundcloud_auth_token == TOKEN


# --- Wirkung auf die Downloads ---------------------------------------------------

def test_process_with_guarded_config_only_links(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUNDCLOUD_AUTH_TOKEN", TOKEN)
    seen = []
    monkeypatch.setattr(m, "download_native", lambda t, inbox, token=None: seen.append(token))
    cfg = m.guard_auth_token(_cfg(tmp_path), FakeSC(), no_telegram=True, check=Check(TokenStatus.INVALID))
    m.process([mk(1, downloadable=True)], cfg, dry_run=False)
    assert seen == [None]


def test_run_discover_passes_the_guarded_config_on(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUNDCLOUD_AUTH_TOKEN", TOKEN)
    got = []
    monkeypatch.setattr(m, "SoundCloudClient", lambda *a, **k: FakeSC())
    monkeypatch.setattr(m, "check_auth_token", lambda token, *, client_id=None: TokenStatus.INVALID)
    monkeypatch.setattr(m, "_discover", lambda cfg, dry_run, no_telegram: got.append(cfg.soundcloud_auth_token) or 5)
    m.run_discover(_cfg(tmp_path), dry_run=False, no_telegram=True)
    assert got == [None]
