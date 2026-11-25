"""Akzeptanztests: Läufe der Cron-Jobs protokollieren, Teil 1 (Helfer und Sicherung) (Issue <N>)."""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from sc_digger.backup import BACKUP_MODE, run_backup_logged
from sc_digger.health import Health, record_job
from sc_digger.models import Config
from sc_digger.schedule import JOBS
from sc_digger.web.status import ALERT_LABELS

ROOT = Path(__file__).resolve().parents[2]


def _runs(db: Path, mode: str) -> list[tuple]:
    con = sqlite3.connect(str(db))
    try:
        return con.execute("SELECT ok, found, error FROM runs WHERE mode=? ORDER BY id", (mode,)).fetchall()
    finally:
        con.close()


def test_zero_is_bad_false_wertet_leere_laeufe_nicht_als_ausfall(tmp_path):
    db = tmp_path / "seen.sqlite"
    with Health(db) as h:
        for _ in range(3):
            h.record("scout", ok=True, found=0)
        assert h.evaluate("scout", 2, zero_is_bad=False) is None
        assert h.evaluate("scout", 2) is not None  # Standard bleibt wie bisher


def test_record_job_schreibt_den_lauf_und_alarmiert_einmal(tmp_path):
    db = tmp_path / "seen.sqlite"
    assert record_job(db, "taste", ok=True, found=0) is None
    assert record_job(db, "taste", ok=False, error="Kaputt", threshold=2) is None
    alarm = record_job(db, "taste", ok=False, error="Kaputt", threshold=2)
    assert alarm is not None and "taste" in alarm and "Kaputt" in alarm
    assert record_job(db, "taste", ok=False, error="Kaputt", threshold=2) is None
    entwarnung = record_job(db, "taste", ok=True, found=0, threshold=2)
    assert entwarnung is not None and "taste" in entwarnung
    assert [r[0] for r in _runs(db, "taste")] == [1, 0, 0, 0, 1]


def test_record_job_wirft_nie(tmp_path):
    kein_db = tmp_path / "ordner"
    kein_db.mkdir()
    assert record_job(kein_db, "backup", ok=True, found=1) is None


def _cfg(tmp_path: Path, target: str) -> Config:
    return Config(raw={
        "state": {"db_path": str(tmp_path / "seen.sqlite"),
                  "track_db_path": str(tmp_path / "tracks.sqlite")},
        "backup": {"target_dir": target, "keep_daily": 7, "keep_weekly": 4},
        "health": {"alert_after_bad_runs": 2},
    })


def test_nicht_eingerichtete_sicherung_wird_protokolliert_und_gemeldet(tmp_path, monkeypatch):
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)
    meldungen: list[str] = []
    cfg = _cfg(tmp_path, "")

    assert run_backup_logged(cfg, today=date(2026, 11, 25), notify=meldungen.append) == 2
    assert meldungen == []
    assert run_backup_logged(cfg, today=date(2026, 11, 26), notify=meldungen.append) == 2

    runs = _runs(tmp_path / "seen.sqlite", BACKUP_MODE)
    assert [r[0] for r in runs] == [0, 0]
    assert "backup.target_dir" in runs[0][2]
    assert len(meldungen) == 1 and "backup.target_dir" in meldungen[0]


def test_erfolgreiche_sicherung_wird_protokolliert(tmp_path, monkeypatch):
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)
    with Health(tmp_path / "seen.sqlite") as h:
        h.record("discover", ok=True, found=1)
    ziel = tmp_path / "backup"
    ziel.mkdir()

    assert run_backup_logged(_cfg(tmp_path, str(ziel)), today=date(2026, 11, 25)) == 0

    assert (ziel / "2026-11-25" / "seen.sqlite").is_file()
    assert _runs(tmp_path / "seen.sqlite", BACKUP_MODE) == [(1, 1, None)]


def test_sicherung_ist_ab_werk_eingeschaltet(tmp_path):
    cfg = Config.load(ROOT / "config.yaml", local=tmp_path / "keine-override.yaml")
    ziel = cfg.raw["backup"]["target_dir"]
    assert ziel == "/data/backup"
    assert Path(cfg.raw["state"]["db_path"]).parent == Path(ziel).parent


def test_zeitplan_und_alarmname_kennen_die_sicherung():
    assert BACKUP_MODE == "backup"
    assert {j.name: j.mode for j in JOBS}["backup"] == "backup"
    assert ALERT_LABELS["backup"] == "Sicherung"
