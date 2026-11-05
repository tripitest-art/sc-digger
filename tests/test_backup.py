"""Eigene Tests für sc_digger/backup.py (Issue #456): CLI, Cron-Eintrag und Wiederherstellung."""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from sc_digger.backup import (
    BackupError,
    backup_sqlite,
    main,
    prune_backups,
    restore_sqlite,
)

ROOT = Path(__file__).resolve().parents[1]
SH = shutil.which("sh")


def _db(path: Path, rows=(1, 2, 3)) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (x INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [(r,) for r in rows])
    con.commit()
    con.close()


def _rows(path: Path) -> list[int]:
    con = sqlite3.connect(path)
    try:
        return [r[0] for r in con.execute("SELECT x FROM t ORDER BY x")]
    finally:
        con.close()


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


# --- Cron-Eintrag in entrypoint.sh ------------------------------------------------


def test_entrypoint_installs_backup_cron_job(tmp_path):
    """entrypoint.sh richtet den täglichen Backup-Lauf um 06:30 ein und schützt vor Duplikaten."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "bot-started"
    _executable(bin_dir / "crontab", "#!/bin/sh\nexit 0\n")
    _executable(bin_dir / "cron", "#!/bin/sh\nexit 0\n")
    _executable(bin_dir / "python", f'#!/bin/sh\necho "$@" > "{marker}"\nexit 0\n')
    for tool in ("grep", "chmod"):
        (bin_dir / tool).symlink_to(shutil.which(tool))

    cron_file = tmp_path / "sc-digger"
    cron_file.write_text("30 7 * * * . /app/cron.env; cd /app && python -m sc_digger.main >> /proc/1/fd/1 2>&1\n", encoding="utf-8")

    script = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    script = script.replace("/app/cron.env", str(tmp_path / "cron.env"))
    script = script.replace("/etc/cron.d/sc-digger", str(cron_file))
    script_path = tmp_path / "entrypoint-test.sh"
    script_path.write_text(script, encoding="utf-8")

    env = {"PATH": str(bin_dir), "HOME": str(tmp_path)}
    proc = subprocess.run([SH, str(script_path)], env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr

    cron_content = cron_file.read_text(encoding="utf-8")
    expected_line = f"30 6 * * * . {tmp_path / 'cron.env'}; cd /app && python -m sc_digger.backup run >> /proc/1/fd/1 2>&1"
    assert expected_line in cron_content

    # Zweiter Lauf darf den Job nicht verdoppeln
    proc2 = subprocess.run([SH, str(script_path)], env=env, capture_output=True, text=True, timeout=30)
    assert proc2.returncode == 0, proc2.stderr
    assert cron_file.read_text(encoding="utf-8").count("sc_digger.backup run") == 1


# --- CLI main(["restore", ...]) ---------------------------------------------------


def test_cli_restore_success(tmp_path, capsys):
    src = tmp_path / "backup.sqlite"
    _db(src, rows=(42,))
    dest = tmp_path / "restored.sqlite"

    ret = main(["restore", str(src), str(dest)])
    assert ret == 0
    assert _rows(dest) == [42]
    out, err = capsys.readouterr()
    assert err == ""


def test_cli_restore_existing_without_force_fails(tmp_path, capsys):
    src = tmp_path / "backup.sqlite"
    _db(src, rows=(1,))
    dest = tmp_path / "seen.sqlite"
    _db(dest, rows=(99,))

    ret = main(["restore", str(src), str(dest)])
    assert ret == 1
    assert _rows(dest) == [99]
    _, err = capsys.readouterr()
    assert "FEHLER: Ziel" in err and "existiert bereits" in err


def test_cli_restore_existing_with_force_succeeds(tmp_path, capsys):
    src = tmp_path / "backup.sqlite"
    _db(src, rows=(1,))
    dest = tmp_path / "seen.sqlite"
    _db(dest, rows=(99,))

    ret = main(["restore", str(src), str(dest), "--force"])
    assert ret == 0
    assert _rows(dest) == [1]
    assert _rows(tmp_path / "seen.sqlite.vor-restore") == [99]
    _, err = capsys.readouterr()
    assert err == ""


def test_cli_restore_broken_backup_fails(tmp_path, capsys):
    src = tmp_path / "corrupt.sqlite"
    src.write_bytes(b"kaputter datenmuell" * 50)
    dest = tmp_path / "out.sqlite"

    ret = main(["restore", str(src), str(dest)])
    assert ret == 1
    assert not dest.exists()
    _, err = capsys.readouterr()
    assert "FEHLER:" in err


def test_cli_restore_missing_source_fails(tmp_path, capsys):
    ret = main(["restore", str(tmp_path / "fehlt.sqlite"), str(tmp_path / "ziel.sqlite")])
    assert ret == 1
    _, err = capsys.readouterr()
    assert "FEHLER: Sicherungsdatei nicht gefunden" in err


# --- CLI main(["run", ...]) -------------------------------------------------------


def test_cli_run_without_target_returns_2(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({"backup": {"target_dir": ""}}), encoding="utf-8")
    assert main(["run", "--config", str(cfg_file)]) == 2


def test_cli_run_success(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    seen = data_dir / "seen.sqlite"
    tracks = data_dir / "tracks.sqlite"
    _db(seen, rows=(1,))
    _db(tracks, rows=(2,))
    backup_target = tmp_path / "backups"

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({
        "state": {"db_path": str(seen), "track_db_path": str(tracks)},
        "backup": {"target_dir": str(backup_target), "keep_daily": 5, "keep_weekly": 2},
    }), encoding="utf-8")

    assert main(["run", "--config", str(cfg_file)]) == 0
    today_str = date.today().isoformat()
    day_folder = backup_target / today_str
    assert day_folder.is_dir()
    assert (day_folder / "seen.sqlite").exists()
    assert (day_folder / "tracks.sqlite").exists()


def test_cli_run_failure_sends_telegram(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    seen = data_dir / "seen.sqlite"
    seen.write_bytes(b"kaputt" * 50)
    backup_target = tmp_path / "backups"

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({
        "state": {"db_path": str(seen), "track_db_path": str(tmp_path / "fehlt.sqlite")},
        "backup": {"target_dir": str(backup_target)},
    }), encoding="utf-8")

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    mock_telegram = MagicMock()
    monkeypatch.setattr("sc_digger.output.telegram_call", mock_telegram)

    assert main(["run", "--config", str(cfg_file)]) == 1
    assert mock_telegram.call_count == 1
    call_args = mock_telegram.call_args[1]
    assert "Sicherung fehlgeschlagen" in call_args["json"]["text"]


def test_cli_run_missing_config_returns_1(tmp_path):
    assert main(["run", "--config", str(tmp_path / "nicht-da.yaml")]) == 1


# --- Edge Cases -------------------------------------------------------------------


def test_backup_sqlite_missing_src_raises(tmp_path):
    with pytest.raises(BackupError, match="Quelldatei nicht gefunden"):
        backup_sqlite(tmp_path / "gibt_es_nicht.sqlite", tmp_path / "dest.sqlite")


def test_restore_sqlite_missing_src_raises(tmp_path):
    with pytest.raises(BackupError, match="Sicherungsdatei nicht gefunden"):
        restore_sqlite(tmp_path / "gibt_es_nicht.sqlite", tmp_path / "dest.sqlite")


def test_prune_backups_empty_dir(tmp_path):
    assert prune_backups(tmp_path) == []
