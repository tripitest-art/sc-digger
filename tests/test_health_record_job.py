"""Eigene Tests zu record_job, zero_is_bad und run_backup_logged (Issue 706)."""
from __future__ import annotations

import sqlite3
from datetime import date

from sc_digger.backup import BACKUP_MODE, run_backup_logged
from sc_digger.health import Health, record_job
from sc_digger.models import Config


def _cfg(tmp_path, target, **extra):
    raw = {"state": {"db_path": str(tmp_path / "seen.sqlite")},
           "backup": {"target_dir": target}}
    raw.update(extra)
    return Config(raw=raw)


def test_hinweis_auf_backup_target_dir_im_alarm(tmp_path):
    db = tmp_path / "seen.sqlite"
    text = None
    for _ in range(2):
        text = record_job(db, "backup", ok=False, error="Sicherung nicht eingerichtet (backup.target_dir leer)")
    assert text and "<code>backup.target_dir</code>" in text and "BETRIEB.md" in text


def test_ohne_db_pfad_wird_nichts_protokolliert(tmp_path, monkeypatch):
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)
    assert run_backup_logged(Config(raw={"backup": {"target_dir": ""}}), today=date(2026, 11, 25)) == 2


def test_fehler_im_notify_wird_abgefangen(tmp_path, monkeypatch):
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)

    def kaputt(_t: str) -> None:
        raise RuntimeError("Telegram weg")

    cfg = _cfg(tmp_path, "")
    assert run_backup_logged(cfg, today=date(2026, 11, 25), notify=kaputt) == 2
    assert run_backup_logged(cfg, today=date(2026, 11, 26), notify=kaputt) == 2


def test_schwelle_aus_health_konfiguration(tmp_path, monkeypatch):
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)
    meldungen: list[str] = []
    cfg = _cfg(tmp_path, "", health={"alert_after_bad_runs": 1})
    run_backup_logged(cfg, today=date(2026, 11, 25), notify=meldungen.append)
    assert len(meldungen) == 1


def test_unbekannter_rueckgabecode(tmp_path, monkeypatch):
    monkeypatch.setattr("sc_digger.backup.run_backup", lambda *a, **k: 7)
    assert run_backup_logged(_cfg(tmp_path, "x"), today=date(2026, 11, 25)) == 7
    con = sqlite3.connect(str(tmp_path / "seen.sqlite"))
    try:
        err = con.execute("SELECT error FROM runs WHERE mode=?", (BACKUP_MODE,)).fetchone()[0]
    finally:
        con.close()
    assert err == "Sicherung beendet mit Code 7"


def test_standard_evaluate_unveraendert(tmp_path):
    with Health(tmp_path / "s.sqlite") as h:
        h.record("discover", ok=True, found=0)
        h.record("discover", ok=True, found=0)
        assert h.evaluate("discover", 2) is not None
