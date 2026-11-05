"""Akzeptanztests: Speicherplatz-Wächter."""
from __future__ import annotations

from types import SimpleNamespace

from sc_digger import main as m
from sc_digger.diskguard import check_disk
from sc_digger.health import Health
from sc_digger.models import Config

from tests.test_modes import CFG, mk

GIB = 1024 ** 3


class Usage:
    def __init__(self, free_gb: float = 50, error: Exception | None = None):
        self.free, self.error, self.calls = int(free_gb * GIB), error, []

    def __call__(self, path):
        self.calls.append(path)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(total=1000 * GIB, used=0, free=self.free)


def _cfg(tmp_path, min_free_gb=20):
    cfg = Config(dict(CFG.raw))
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path / "coll"),
                           "inbox_dir": str(tmp_path / "inbox")}
    cfg.raw["health"] = {"alert_after_bad_runs": 2, "min_free_gb": min_free_gb}
    return cfg


# --- check_disk ------------------------------------------------------------------

def test_check_disk_reports_free_space_against_the_minimum(tmp_path):
    st = check_disk(tmp_path, 20, usage=Usage(30))
    assert st.ok is True and st.free_gb == 30.0 and st.min_free_gb == 20.0
    assert check_disk(tmp_path, 20, usage=Usage(19.5)).ok is False
    assert check_disk(tmp_path, 20, usage=Usage(20)).ok is True


def test_check_disk_measures_the_nearest_existing_parent(tmp_path):
    usage = Usage(30)
    st = check_disk(tmp_path / "inbox" / "fehlt", 20, usage=usage)
    assert usage.calls == [str(tmp_path)]
    assert st.path == str(tmp_path / "inbox" / "fehlt")


# --- Config.without_downloads ----------------------------------------------------

def test_without_downloads_pauses_only_the_copy(tmp_path):
    cfg = _cfg(tmp_path)
    off = cfg.without_downloads()
    assert off.downloads_paused is True
    assert cfg.downloads_paused is False
    assert off["state"] is cfg["state"]


# --- guard_disk ------------------------------------------------------------------

def test_guard_disk_is_off_by_default(tmp_path):
    for value in (0, None):
        cfg = _cfg(tmp_path, min_free_gb=value)
        usage = Usage(1)
        assert m.guard_disk(cfg, no_telegram=True, usage=usage) is cfg
        assert usage.calls == []
    cfg = _cfg(tmp_path)
    del cfg.raw["health"]["min_free_gb"]
    assert m.guard_disk(cfg, no_telegram=True, usage=Usage(1)) is cfg
    with Health(tmp_path / "seen.sqlite") as health:
        assert health.evaluate("disk", 1) is None


def test_guard_disk_with_enough_space_keeps_downloads(tmp_path):
    out = m.guard_disk(_cfg(tmp_path), no_telegram=True, usage=Usage(50))
    assert out.downloads_paused is False


def test_guard_disk_low_space_pauses_and_alarms_once_then_recovers(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    sent = []
    monkeypatch.setattr(m, "send_telegram", lambda cfg, msgs, *a, **k: sent.append(msgs))
    out = m.guard_disk(cfg, no_telegram=False, usage=Usage(5))
    assert out.downloads_paused is True
    assert len(sent) == 1
    alarm = "\n".join(sent[0])
    assert "disk" in alarm and "Speicher knapp: 5.0 GB frei, Minimum 20.0 GB" in alarm
    assert "pausiert" in alarm
    assert m.guard_disk(cfg, no_telegram=False, usage=Usage(5)).downloads_paused is True
    assert len(sent) == 1
    assert m.guard_disk(cfg, no_telegram=False, usage=Usage(50)).downloads_paused is False
    assert len(sent) == 2
    assert "läuft wieder" in "\n".join(sent[1])


def test_guard_disk_unreadable_inbox_pauses_too(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(m, "send_telegram", lambda cfg, msgs, *a, **k: sent.append(msgs))
    out = m.guard_disk(_cfg(tmp_path), no_telegram=False, usage=Usage(error=OSError("Stale file handle")))
    assert out.downloads_paused is True
    assert "Inbox nicht prüfbar (OSError)" in "\n".join(sent[0])


# --- Wirkung ---------------------------------------------------------------------

def test_process_with_paused_downloads_loads_nothing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(m, "download_native", lambda *a, **k: calls.append("native"))
    monkeypatch.setattr(m, "download_cloud", lambda *a, **k: calls.append("cloud"))
    cfg = _cfg(tmp_path)
    cfg.raw["download"]["auto_download_native_only"] = False
    paused = cfg.without_downloads()
    cloud = mk(2, downloadable=True, has_downloads_left=False,
               description="Download: https://drive.google.com/file/d/abc/view")
    fresh, dupes = m.process([mk(1, downloadable=True), cloud], paused, dry_run=False)
    assert calls == []
    assert [t.id for t in fresh] == [1, 2]
    assert not (tmp_path / "inbox").exists()


def test_run_discover_passes_the_paused_config_on(tmp_path, monkeypatch):
    got = []
    monkeypatch.setattr(m.shutil, "disk_usage", Usage(1))
    monkeypatch.setattr(m, "_discover", lambda cfg, dry_run, no_telegram: got.append(cfg.downloads_paused) or 5)
    m.run_discover(_cfg(tmp_path), dry_run=False, no_telegram=True)
    assert got == [True]
