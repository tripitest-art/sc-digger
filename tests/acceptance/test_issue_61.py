"""Akzeptanztests: Discovery läuft weiter, wenn einzelne SoundCloud-Quellen scheitern."""
import sqlite3
from pathlib import Path

import pytest

from sc_digger import main as m
from sc_digger import soundcloud as scmod
from sc_digger.health import Health
from sc_digger.models import Config
from sc_digger.soundcloud import ClientIdError, RateLimitError, SoundCloudClient, SoundCloudError
from tests.test_modes import mk

CFG = Config.load(Path(__file__).resolve().parents[2] / "config.yaml")
TAGS = ["schranz", "hardtechno", "industrial techno"]


def _cfg(tmp_path, followed=()):
    cfg = Config(dict(CFG.raw))
    cfg.raw["search"] = {**CFG["search"], "tags": list(TAGS),
                         "followed_users": list(followed), "reference_accounts": []}
    cfg.raw["state"] = {**CFG["state"], "db_path": str(tmp_path / "state.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path / "coll"),
                           "inbox_dir": str(tmp_path / "inbox")}
    cfg.raw["health"] = {"alert_after_bad_runs": 2}
    return cfg


class FakeSC:
    """Tag-Suche mit einstellbarem Fehler je Tag, ohne Netzwerk."""

    def __init__(self, errors):
        self.errors, self.calls = errors, []

    def search_tag(self, tag, age, limit):
        self.calls.append(tag)
        if tag in self.errors:
            raise self.errors[tag]
        return [mk(i, playback_count=2000, likes_count=40 + i) for i in range(40)]

    def user_uploads(self, url, age):
        self.calls.append(url)
        return []

    def reference_activity(self, url, age, limit=50):
        self.calls.append(url)
        return []


class FakeResponse:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text

    def json(self):
        return {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, status=200, text=""):
        self.status, self.text, self.headers = status, text, {}

    def get(self, url, **kw):
        return FakeResponse(self.status, self.text)


def test_error_types_are_soundcloud_errors():
    assert issubclass(ClientIdError, SoundCloudError)
    assert issubclass(RateLimitError, SoundCloudError)


def test_missing_client_id_raises_client_id_error():
    sc = SoundCloudClient(request_delay=0)
    sc.s = FakeSession(200, "<html>kein Skript</html>")
    with pytest.raises(ClientIdError):
        sc.search_tag("schranz", 14, 10)


def test_persistent_429_raises_rate_limit_error(monkeypatch):
    monkeypatch.setattr(scmod.time, "sleep", lambda s: None)
    sc = SoundCloudClient(request_delay=0)
    sc.client_id = "x" * 32
    sc.s = FakeSession(429)
    with pytest.raises(RateLimitError):
        sc.search_tag("schranz", 14, 10)


def test_one_failing_tag_does_not_stop_discovery(tmp_path, monkeypatch, capsys):
    fake = FakeSC({"hardtechno": SoundCloudError("Request fehlgeschlagen")})
    monkeypatch.setattr(m, "SoundCloudClient", lambda *a, **k: fake)
    found = m._discover(_cfg(tmp_path), True, True)
    assert found == 40
    assert fake.calls == TAGS
    out = capsys.readouterr().out
    assert "1 von 3 Quellen" in out
    assert "hardtechno" in out


def test_client_id_error_skips_remaining_sources(tmp_path, monkeypatch, capsys):
    fake = FakeSC({"hardtechno": ClientIdError("client_id konnte nicht ermittelt werden")})
    monkeypatch.setattr(m, "SoundCloudClient", lambda *a, **k: fake)
    found = m._discover(_cfg(tmp_path, followed=["https://soundcloud.com/label"]), True, True)
    assert found == 40
    assert fake.calls == ["schranz", "hardtechno"]
    assert "übersprungen" in capsys.readouterr().out


def test_rate_limit_skips_remaining_sources(tmp_path, monkeypatch, capsys):
    fake = FakeSC({"schranz": RateLimitError("429")})
    monkeypatch.setattr(m, "SoundCloudClient", lambda *a, **k: fake)
    with pytest.raises(RateLimitError):
        m._discover(_cfg(tmp_path), True, True)
    assert fake.calls == ["schranz"]


def test_all_sources_failing_is_recorded_as_failed_run(tmp_path, monkeypatch):
    errors = {t: SoundCloudError("Request fehlgeschlagen") for t in TAGS}
    monkeypatch.setattr(m, "SoundCloudClient", lambda *a, **k: FakeSC(errors))
    cfg = _cfg(tmp_path)
    with pytest.raises(SoundCloudError):
        m.run_discover(cfg, dry_run=False, no_telegram=True)
    db = sqlite3.connect(tmp_path / "state.sqlite")
    assert db.execute("SELECT ok, found FROM runs").fetchall() == [(0, 0)]


def test_health_alarm_names_rate_limit(tmp_path):
    with Health(tmp_path / "h.sqlite") as h:
        for _ in range(2):
            h.record("discover", ok=False, found=0, error="RateLimitError: SoundCloud antwortet mit 429")
        msg = h.evaluate("discover", 2)
    assert msg and "🚨" in msg
    assert "drosselt" in msg
