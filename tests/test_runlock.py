"""Eigene Tests zur Lauf-Sperre (Issue #455), ergänzend zu tests/acceptance/test_issue_455.py."""
from __future__ import annotations

import logging
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from sc_digger import main as m
from sc_digger.health import Health
from sc_digger.models import Config
from sc_digger.runlock import LOCK_NAME, RunLockBusy, busy_text, read_holder, run_lock

ROOT = Path(__file__).resolve().parents[1]


class Ticker:
    def __init__(self):
        self.t = 0.0
        self.waits: list[float] = []

    def clock(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.t += seconds


def _cfg(tmp_path, **state):
    return Config({"state": {"db_path": str(tmp_path / "seen.sqlite"), **state},
                   "health": {"alert_after_bad_runs": 5}})


def test_read_holder_ignores_broken_or_odd_files(tmp_path):
    lock = tmp_path / LOCK_NAME
    for content in ("{kaputt", "[1, 2]", "{}", "   "):
        lock.write_text(content, encoding="utf-8")
        assert read_holder(tmp_path) is None
    assert read_holder(tmp_path / "gibt-es-nicht") is None


def test_busy_text_tolerates_missing_keys():
    assert busy_text({}) == "Lauf-Sperre belegt"
    assert busy_text({"mode": "scout"}) == "Lauf-Sperre belegt: scout läuft seit ?"


def test_busy_exception_without_holder(tmp_path):
    exc = RunLockBusy(None)
    assert exc.holder is None
    assert str(exc) == "Lauf-Sperre belegt"


def test_lock_dir_is_created(tmp_path):
    target = tmp_path / "a" / "b"
    with run_lock(target, "audit") as path:
        assert path == target / LOCK_NAME
        assert path.exists()


def test_lock_excludes_a_second_process(tmp_path):
    """flock muss zwischen echten Prozessen wirken (Cron-Jobs sind getrennte Prozesse)."""
    child = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(ROOT)!r})
        from sc_digger.runlock import run_lock
        with run_lock({str(tmp_path)!r}, "audit"):
            print("gesperrt", flush=True)
            sys.stdin.readline()
    """)
    proc = subprocess.Popen([sys.executable, "-c", child], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "gesperrt"
        with pytest.raises(RunLockBusy) as exc:
            with run_lock(tmp_path, "discover"):
                pass
        assert exc.value.holder["mode"] == "audit"
        assert exc.value.holder["pid"] == proc.pid
    finally:
        proc.stdin.write("\n")
        proc.stdin.flush()
        proc.wait(timeout=30)
    with run_lock(tmp_path, "discover"):  # nach Prozessende frei
        pass


def test_default_wait_is_1800_seconds(tmp_path):
    t = Ticker()
    with run_lock(tmp_path, "audit"):
        ok = m.run_locked(_cfg(tmp_path), "scout", lambda: None, no_telegram=True,
                          sleep=t.sleep, clock=t.clock)
    assert ok is False
    assert sum(t.waits) == 1800
    assert set(t.waits) == {5.0}


def test_busy_non_discover_writes_no_health_entry(tmp_path, caplog):
    caplog.set_level(logging.ERROR)
    with run_lock(tmp_path, "discover"):
        assert m.run_locked(_cfg(tmp_path, lock_wait_s=0), "audit", lambda: None,
                            no_telegram=True) is False
    assert "Lauf übersprungen (audit): Lauf-Sperre belegt: discover" in caplog.text
    with Health(tmp_path / "seen.sqlite") as health:
        assert health.evaluate("discover", 1) is None


def test_busy_discover_sends_alarm_after_threshold(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(m, "send_telegram", lambda cfg, msgs, *a, **k: sent.append(msgs))
    cfg = Config({"state": {"db_path": str(tmp_path / "seen.sqlite"), "lock_wait_s": 0},
                  "health": {"alert_after_bad_runs": 1}})
    with run_lock(tmp_path, "audit"):
        m.run_locked(cfg, "discover", lambda: None, no_telegram=False)
    assert len(sent) == 1
    assert "Lauf-Sperre belegt: audit" in "\n".join(sent[0])


def test_cli_stats_runs_while_lock_is_held(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(f"state:\n  db_path: {tmp_path / 'seen.sqlite'}\n  lock_wait_s: 0\n",
                        encoding="utf-8")
    calls = []
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)
    monkeypatch.setattr(m, "run_stats", lambda cfg, **k: calls.append(k["days"]))
    monkeypatch.setattr(sys, "argv", ["sc-digger", "stats", "--config", str(cfg_file), "--days", "3"])
    with run_lock(tmp_path, "discover"):
        m.cli()
    assert calls == [3]


def test_cli_releases_lock_after_failing_run(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(f"state:\n  db_path: {tmp_path / 'seen.sqlite'}\n", encoding="utf-8")
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)

    def boom(cfg):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(m, "write_rekordbox_xml", boom)
    monkeypatch.setattr(sys, "argv", ["sc-digger", "rekordbox", "--config", str(cfg_file)])
    with pytest.raises(SystemExit) as exc:
        m.cli()
    assert exc.value.code == 1
    with run_lock(tmp_path, "x"):
        pass
