"""Eigene Tests für main.run_logged (Issue #707)."""
import sqlite3

import pytest

import sc_digger.main as m
from sc_digger.models import Config


def _cfg(tmp_path, schwelle=2):
    return Config(raw={"state": {"db_path": str(tmp_path / "seen.sqlite")},
                       "health": {"alert_after_bad_runs": schwelle}})


def _zeilen(tmp_path):
    con = sqlite3.connect(str(tmp_path / "seen.sqlite"))
    try:
        return con.execute("SELECT mode, ok, found FROM runs ORDER BY id").fetchall()
    finally:
        con.close()


def test_bool_zaehlt_als_ein_fund(tmp_path):
    assert m.run_logged(_cfg(tmp_path), "taste", lambda: True, no_telegram=True) is True
    assert _zeilen(tmp_path) == [("taste", 1, 1)]


def test_alarm_nach_wiederholtem_fehler_geht_an_notify(tmp_path, monkeypatch):
    meldungen = []
    monkeypatch.setattr(m, "_notify_health", lambda cfg, text, no_tg: meldungen.append(text))

    def kaputt():
        raise ValueError("x")

    for _ in range(2):
        with pytest.raises(ValueError):
            m.run_logged(_cfg(tmp_path), "audit", kaputt, no_telegram=True)
    assert meldungen[0] is None
    assert meldungen[1] and "audit" in meldungen[1]


def test_ohne_state_wird_nichts_angelegt(tmp_path):
    assert m.run_logged(Config(raw={"state": {}}), "scout", lambda: 5, no_telegram=True) == 5
    assert not (tmp_path / "seen.sqlite").exists()
