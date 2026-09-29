"""Unit-Tests für den Container-Healthcheck (sc_digger.healthcheck)."""
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

from sc_digger import healthcheck as hc
from sc_digger.healthcheck import CheckResult
from sc_digger.models import Config


def write_test_cfg(tmp_path: Path) -> Path:
    cfg_data = {
        "state": {
            "db_path": str(tmp_path / "seen.sqlite"),
            "track_db_path": str(tmp_path / "tracks.sqlite"),
        },
        "health": {
            "max_hours_since_run": 36,
        },
    }
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump(cfg_data), encoding="utf-8")
    return cfg_file


def test_alert_message_formatting_and_no_credentials(tmp_path, monkeypatch):
    """Alarmtext formatiert fehlgeschlagene Prüfungen als '<name>: <detail>' und leakt keine Secrets."""
    secret_token = "SECRET_TOKEN_987654321"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", secret_token)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345678")

    state_file = tmp_path / "state.json"
    results = [
        CheckResult("cron", False, "cron läuft nicht"),
        CheckResult("letzter_lauf", False, "letzter Lauf vor 40.0 Stunden"),
        CheckResult("state_db", True, "ok"),
    ]

    msg = hc.alert_message(state_file, results)
    assert msg is not None
    assert "cron: cron läuft nicht" in msg
    assert "letzter_lauf: letzter Lauf vor 40.0 Stunden" in msg
    assert secret_token not in msg

    # Erneuter Aufruf mit gleichem Zustand erzeugt keine Meldung
    assert hc.alert_message(state_file, results) is None

    # Entwarnung enthält "wieder" und keine Secrets
    good_results = [
        CheckResult("cron", True, "ok"),
        CheckResult("letzter_lauf", True, "ok"),
        CheckResult("state_db", True, "ok"),
    ]
    recovery = hc.alert_message(state_file, good_results)
    assert recovery is not None
    assert "wieder" in recovery
    assert secret_token not in recovery


def test_cron_unreadable_comm_skipped(tmp_path):
    """Nicht lesbare comm-Dateien werden übersprungen; cron wird dennoch gefunden."""
    proc_root = tmp_path / "proc"
    proc_root.mkdir()

    # PID 1: unlesbare comm-Datei
    pid1 = proc_root / "1"
    pid1.mkdir()
    comm1 = pid1 / "comm"
    comm1.write_text("unreadable\n")

    # PID 2: cron Prozess
    pid2 = proc_root / "2"
    pid2.mkdir()
    comm2 = pid2 / "comm"
    comm2.write_text("cron\n")

    original_read_text = Path.read_text

    def mock_read_text(self, *args, **kwargs):
        if self == comm1:
            raise PermissionError("Keine Leseberechtigung")
        return original_read_text(self, *args, **kwargs)

    with patch.object(Path, "read_text", side_effect=mock_read_text, autospec=True):
        res = hc.check_cron(proc_root=proc_root)
        assert res.ok is True
        assert res.name == "cron"


def test_cron_unreadable_comm_without_cron(tmp_path):
    """Nicht lesbare comm-Datei ohne cron-Prozess führt zu cron läuft nicht (kein Absturz)."""
    proc_root = tmp_path / "proc"
    proc_root.mkdir()

    pid1 = proc_root / "10"
    pid1.mkdir()
    comm1 = pid1 / "comm"
    comm1.write_text("other\n")

    original_read_text = Path.read_text

    def mock_read_text(self, *args, **kwargs):
        if self == comm1:
            raise OSError("I/O Fehler")
        return original_read_text(self, *args, **kwargs)

    with patch.object(Path, "read_text", side_effect=mock_read_text, autospec=True):
        res = hc.check_cron(proc_root=proc_root)
        assert res.ok is False
        assert res.name == "cron"
        assert res.detail == "cron läuft nicht"


def test_missing_runs_table_is_ok(tmp_path):
    """Fehlende Tabelle 'runs' in existierender DB gilt als 'ok' ('noch kein Lauf')."""
    db_path = tmp_path / "seen.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE other_table (id INTEGER PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()

    res = hc.check_last_run(db_path, max_age_hours=36)
    assert res.ok is True
    assert res.name == "letzter_lauf"
    assert res.detail == "noch kein Lauf"


def test_check_sqlite_corrupt(tmp_path):
    """Defekte SQLite-DB wird als ungesund erkannt."""
    corrupt_db = tmp_path / "corrupt.sqlite"
    corrupt_db.write_bytes(b"Dies ist keine SQLite-Datenbank" * 50)

    res = hc.check_sqlite(corrupt_db, "state_db")
    assert res.ok is False
    assert res.name == "state_db"
    assert "DB-Fehler" in res.detail or "DB beschädigt" in res.detail


def test_main_without_telegram_credentials(tmp_path, monkeypatch, capsys):
    """Ohne gesetzte Telegram-Tokens wird kein telegram_call ausgeführt und der Status korrekt ausgegeben."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    calls = []
    monkeypatch.setattr(hc, "telegram_call", lambda *a, **k: calls.append((a, k)))

    cfg_file = write_test_cfg(tmp_path)

    # Proc ohne cron
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    pid1 = proc_root / "1"
    pid1.mkdir()
    (pid1 / "comm").write_text("python\n")

    exit_code = hc.main(["--config", str(cfg_file)], proc_root=proc_root)

    # Exit code muss 1 sein (da cron nicht läuft)
    assert exit_code == 1
    # telegram_call darf nicht aufgerufen worden sein
    assert len(calls) == 0

    # Output prüfen
    captured = capsys.readouterr()
    assert "cron: FEHLER - cron läuft nicht" in captured.out

    # Status-Datei wurde trotzdem angelegt
    state_file = tmp_path / "container_health.json"
    assert state_file.exists()


def test_run_checks_order_and_types(tmp_path):
    """run_checks führt cron, letzter_lauf, state_db und track_db in dieser Reihenfolge aus."""
    cfg_file = write_test_cfg(tmp_path)
    cfg = Config.load(cfg_file)

    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    pid = proc_root / "1"
    pid.mkdir()
    (pid / "comm").write_text("cron\n")

    results = hc.run_checks(cfg, proc_root=proc_root)
    names = [r.name for r in results]
    assert names == ["cron", "letzter_lauf", "state_db", "track_db"]
    assert all(r.ok for r in results)
