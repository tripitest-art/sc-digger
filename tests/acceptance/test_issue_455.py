"""Akzeptanztests: Lauf-Sperre für schreibende CLI-Läufe (Teil 1)."""
from __future__ import annotations

import logging
import sys
from contextlib import ExitStack

import pytest

from sc_digger import main as main_mod
from sc_digger.health import Health
from sc_digger.models import Config
from sc_digger.output import State
from sc_digger.runlock import RunLockBusy, busy_text, read_holder, run_lock


class Ticker:
    """Künstliche Uhr: sleep schiebt die Zeit vor."""

    def __init__(self):
        self.t = 0.0
        self.waits: list[float] = []

    def clock(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.t += seconds


def _cfg(tmp_path, wait_s=0):
    return Config({
        "state": {"db_path": str(tmp_path / "data" / "seen.sqlite"), "lock_wait_s": wait_s},
        "health": {"alert_after_bad_runs": 5},
    })


def test_second_lock_is_refused_and_names_the_holder(tmp_path):
    with run_lock(tmp_path, "audit"):
        with pytest.raises(RunLockBusy) as exc:
            with run_lock(tmp_path, "discover"):
                pass
    assert exc.value.holder["mode"] == "audit"
    assert "audit" in str(exc.value)
    with run_lock(tmp_path, "discover"):  # danach wieder frei
        pass


def test_holder_is_readable_while_held_and_cleared_after(tmp_path):
    assert read_holder(tmp_path) is None
    with run_lock(tmp_path, "scout") as path:
        holder = read_holder(tmp_path)
        assert holder["mode"] == "scout"
        assert isinstance(holder["pid"], int)
        assert holder["started"]
        assert path == tmp_path / "pipeline.lock"
    assert read_holder(tmp_path) is None
    assert (tmp_path / "pipeline.lock").exists()


def test_lock_is_released_when_the_body_raises(tmp_path):
    with pytest.raises(ValueError):
        with run_lock(tmp_path, "audit"):
            raise ValueError("kaputt")
    with run_lock(tmp_path, "discover"):
        pass


def test_busy_text():
    assert busy_text(None) == "Lauf-Sperre belegt"
    assert busy_text({"mode": "audit", "started": "2026-11-05T06:45:00"}) == (
        "Lauf-Sperre belegt: audit läuft seit 2026-11-05T06:45:00")


def test_waits_in_poll_steps_until_wait_s_is_used_up(tmp_path):
    t = Ticker()
    with run_lock(tmp_path, "audit"):
        with pytest.raises(RunLockBusy):
            with run_lock(tmp_path, "discover", wait_s=10, poll_s=4, sleep=t.sleep, clock=t.clock):
                pass
    assert t.waits == [4, 4, 4]


def test_wait_zero_tries_once_without_sleeping(tmp_path):
    t = Ticker()
    with run_lock(tmp_path, "audit"):
        with pytest.raises(RunLockBusy):
            with run_lock(tmp_path, "discover", sleep=t.sleep, clock=t.clock):
                pass
    assert t.waits == []


def test_gets_the_lock_once_the_holder_finishes(tmp_path):
    holder = ExitStack()
    holder.enter_context(run_lock(tmp_path, "audit"))
    ran = []
    with run_lock(tmp_path, "discover", wait_s=60, poll_s=5, sleep=lambda s: holder.close(),
                  clock=lambda: 0.0):
        ran.append(read_holder(tmp_path)["mode"])
    assert ran == ["discover"]


def test_run_locked_runs_fn_and_frees_the_lock(tmp_path):
    cfg = _cfg(tmp_path)
    calls = []
    assert main_mod.run_locked(cfg, "audit", lambda: calls.append(1), no_telegram=True) is True
    assert calls == [1]
    with run_lock(tmp_path / "data", "x"):
        pass


def test_run_locked_passes_exceptions_through_and_frees_the_lock(tmp_path):
    cfg = _cfg(tmp_path)

    def boom():
        raise RuntimeError("Fehler im Lauf")

    with pytest.raises(RuntimeError):
        main_mod.run_locked(cfg, "scout", boom, no_telegram=True)
    with run_lock(tmp_path / "data", "x"):
        pass


def test_run_locked_skips_fn_when_busy_and_logs_discover_as_failed_run(tmp_path):
    cfg = _cfg(tmp_path)
    calls = []
    with run_lock(tmp_path / "data", "audit"):
        ok = main_mod.run_locked(cfg, "discover", lambda: calls.append(1), no_telegram=True)
    assert ok is False
    assert calls == []
    with Health(tmp_path / "data" / "seen.sqlite") as health:
        alarm = health.evaluate("discover", 1)
    assert alarm is not None
    assert "Lauf-Sperre belegt: audit" in alarm


def test_run_locked_waits_lock_wait_s_from_config_in_5s_steps(tmp_path):
    cfg = _cfg(tmp_path, wait_s=30)
    t = Ticker()
    with run_lock(tmp_path / "data", "audit"):
        ok = main_mod.run_locked(cfg, "scout", lambda: None, no_telegram=True,
                                 sleep=t.sleep, clock=t.clock)
    assert ok is False
    assert t.waits == [5.0] * 6


def test_unusable_lock_dir_runs_without_lock_and_warns(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    blocker = tmp_path / "keinordner"
    blocker.write_text("x", encoding="utf-8")
    cfg = Config({"state": {"db_path": str(blocker / "seen.sqlite"), "lock_wait_s": 0}})
    calls = []
    assert main_mod.run_locked(cfg, "audit", lambda: calls.append(1), no_telegram=True) is True
    assert calls == [1]
    assert "Lauf-Sperre nicht verfügbar" in caplog.text


def test_config_without_state_runs_without_lock(caplog):
    caplog.set_level(logging.WARNING)
    calls = []
    assert main_mod.run_locked(Config({}), "audit", lambda: calls.append(1), no_telegram=True) is True
    assert calls == [1]
    assert "Lauf-Sperre nicht verfügbar" in caplog.text


def test_oserror_from_fn_is_not_swallowed(tmp_path):
    cfg = _cfg(tmp_path)

    def fn():
        raise OSError("Platte voll")

    with pytest.raises(OSError):
        main_mod.run_locked(cfg, "audit", fn, no_telegram=True)


def test_stats_needs_no_lock(tmp_path):
    cfg = _cfg(tmp_path)
    calls = []
    with run_lock(tmp_path / "data", "audit"):
        assert main_mod.run_locked(cfg, "stats", lambda: calls.append(1), no_telegram=True) is True
    assert calls == [1]


def test_cli_exits_75_when_another_run_holds_the_lock(tmp_path, monkeypatch):
    data = tmp_path / "data"
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        f"state:\n  db_path: {data / 'seen.sqlite'}\n  track_db_path: {data / 'tracks.sqlite'}\n"
        "  lock_wait_s: 0\n", encoding="utf-8")
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)
    monkeypatch.setattr(sys, "argv", ["sc-digger", "rekordbox", "--config", str(cfg_file), "--no-telegram"])
    with run_lock(data, "audit"):
        with pytest.raises(SystemExit) as exc:
            main_mod.cli()
    assert exc.value.code == 75


def test_state_db_uses_wal(tmp_path):
    with State(tmp_path / "seen.sqlite") as state:
        assert state.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
