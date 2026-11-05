"""Tests für den Speicherplatz-Wächter (Issue #459)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from sc_digger import main as m
from sc_digger.diskguard import DiskStatus, check_disk, GB
from sc_digger.health import Health
from sc_digger.models import Config


class DummyUsage:
    def __init__(self, free_gb: float = 50.0, error: Exception | None = None):
        self.free = int(free_gb * GB)
        self.error = error
        self.calls: list[str] = []

    def __call__(self, path: str):
        self.calls.append(path)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(total=1000 * GB, used=0, free=self.free)


def _make_cfg(tmp_path: Path, min_free_gb: float = 20.0) -> Config:
    return Config({
        "state": {
            "db_path": str(tmp_path / "seen.sqlite"),
            "track_db_path": str(tmp_path / "tracks.sqlite"),
        },
        "download": {
            "inbox_dir": str(tmp_path / "inbox"),
            "collection_dir": str(tmp_path / "coll"),
        },
        "health": {
            "alert_after_bad_runs": 2,
            "min_free_gb": min_free_gb,
        },
    })


# --- DiskStatus & check_disk -------------------------------------------------

def test_disk_status_properties():
    ok_st = DiskStatus(path="/dummy", free_gb=20.0, min_free_gb=20.0)
    assert ok_st.ok is True
    assert ok_st.free_gb == 20.0

    low_st = DiskStatus(path="/dummy", free_gb=19.9, min_free_gb=20.0)
    assert low_st.ok is False

    above_st = DiskStatus(path="/dummy", free_gb=25.0, min_free_gb=20.0)
    assert above_st.ok is True


def test_check_disk_existing_dir(tmp_path):
    sub = tmp_path / "existing"
    sub.mkdir()
    usage = DummyUsage(free_gb=15.5)
    st = check_disk(sub, 10.0, usage=usage)
    assert usage.calls == [str(sub)]
    assert st.path == str(sub)
    assert st.free_gb == 15.5
    assert st.min_free_gb == 10.0
    assert st.ok is True


def test_check_disk_deeply_nested_missing_dir(tmp_path):
    deep = tmp_path / "a" / "b" / "c" / "d"
    usage = DummyUsage(free_gb=10.0)
    st = check_disk(deep, 5.0, usage=usage)
    assert usage.calls == [str(tmp_path)]
    assert st.path == str(deep)
    assert st.ok is True


def test_check_disk_propagates_oserror(tmp_path):
    usage = DummyUsage(error=PermissionError("Kein Zugriff"))
    with pytest.raises(PermissionError):
        check_disk(tmp_path, 20.0, usage=usage)


# --- Config helpers ----------------------------------------------------------

def test_config_preserves_both_guards(tmp_path):
    cfg = _make_cfg(tmp_path)
    assert cfg.downloads_paused is False

    no_auth = cfg.without_auth_token()
    assert no_auth.soundcloud_auth_token is None
    assert no_auth.downloads_paused is False

    no_dl = no_auth.without_downloads()
    assert no_dl.downloads_paused is True
    assert no_dl.soundcloud_auth_token is None

    no_auth_again = no_dl.without_auth_token()
    assert no_auth_again.downloads_paused is True
    assert no_auth_again.soundcloud_auth_token is None


# --- guard_disk & edge cases -------------------------------------------------

def test_guard_disk_missing_or_invalid_health_config(tmp_path):
    cfg1 = Config({"download": {"inbox_dir": str(tmp_path / "inbox")}})
    usage = DummyUsage(1)
    assert m.guard_disk(cfg1, no_telegram=True, usage=usage) is cfg1
    assert usage.calls == []

    cfg2 = Config({"health": {"min_free_gb": "ungueltig"}, "download": {"inbox_dir": str(tmp_path / "inbox")}})
    assert m.guard_disk(cfg2, no_telegram=True, usage=usage) is cfg2
    assert usage.calls == []

    cfg3 = Config({"health": {"min_free_gb": -5.0}, "download": {"inbox_dir": str(tmp_path / "inbox")}})
    assert m.guard_disk(cfg3, no_telegram=True, usage=usage) is cfg3
    assert usage.calls == []


def test_guard_disk_logs_error_on_low_space_and_oserror(tmp_path, caplog):
    cfg = _make_cfg(tmp_path, min_free_gb=20.0)

    with caplog.at_level("ERROR"):
        res = m.guard_disk(cfg, no_telegram=True, usage=DummyUsage(5.0))
        assert res.downloads_paused is True
        assert any("Speicher knapp: 5.0 GB frei" in rec.message for rec in caplog.records)

    caplog.clear()
    with caplog.at_level("ERROR"):
        res = m.guard_disk(cfg, no_telegram=True, usage=DummyUsage(error=PermissionError("Permission denied")))
        assert res.downloads_paused is True
        assert any("Inbox nicht prüfbar (PermissionError)" in rec.message for rec in caplog.records)


# --- run_discover dry-run does not guard disk --------------------------------

def test_run_discover_dry_run_does_not_check_disk(tmp_path, monkeypatch):
    cfg = _make_cfg(tmp_path, min_free_gb=20.0)
    usage = DummyUsage(1.0)
    monkeypatch.setattr(m.shutil, "disk_usage", usage)
    discovered = []
    monkeypatch.setattr(m, "_discover", lambda c, d, n: discovered.append((c.downloads_paused, d)))

    m.run_discover(cfg, dry_run=True, no_telegram=True)
    assert usage.calls == []
    assert discovered == [(False, True)]


# --- Health.evaluate hint lines ----------------------------------------------

def test_health_evaluate_disk_space_hints(tmp_path):
    db_path = tmp_path / "seen.sqlite"
    with Health(db_path) as h:
        h.record("disk", ok=False, found=0, error="Speicher knapp: 4.2 GB frei, Minimum 20.0 GB")
        msg = h.evaluate("disk", 1)
        assert msg is not None
        assert "🚨 <b>sc-digger: disk liefert seit 1 Läufen nichts</b>" in msg
        assert "Speicher knapp: 4.2 GB frei" in msg
        assert "Downloads sind pausiert, bis wieder genug Platz frei ist → Inbox und <code>_rejected</code> aufräumen." in msg


def test_health_evaluate_inbox_unreadable_hints(tmp_path):
    db_path = tmp_path / "seen.sqlite"
    with Health(db_path) as h:
        h.record("disk", ok=False, found=0, error="Inbox nicht prüfbar (OSError)")
        msg = h.evaluate("disk", 1)
        assert msg is not None
        assert "Inbox nicht prüfbar (OSError)" in msg
        assert "Downloads sind pausiert, bis wieder genug Platz frei ist → Inbox und <code>_rejected</code> aufräumen." in msg


def test_health_evaluate_other_error_does_not_contain_disk_hint(tmp_path):
    db_path = tmp_path / "seen.sqlite"
    with Health(db_path) as h:
        h.record("disk", ok=False, found=0, error="Unbekannter anderer Fehler")
        msg = h.evaluate("disk", 1)
        assert msg is not None
        assert "Downloads sind pausiert" not in msg
