"""Eigene Tests zur Alarmliste der Statusseite (Issue #503)."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.health import Health
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.status import _read_active_alerts, collect_status

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
AUTH = ("x", "pw-alarme")


def _cfg(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def _seed(tmp_path, sql, params=()):
    Health(tmp_path / "seen.sqlite").db.close()
    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        db.execute(sql, params)


def test_fehlertext_wird_redigiert(tmp_path):
    # Token-Form zur Laufzeit zusammensetzen, damit der CI-Grep nichts im Quelltext findet.
    geheim = "A" * 35
    token = "bot" + "123456789" + ":" + geheim
    _seed(
        tmp_path,
        "INSERT INTO runs (mode, finished_at, ok, found, error) VALUES (?, ?, 0, 0, ?)",
        ("sc_login", "2026-11-06 07:00:00", f"{token} fehlgeschlagen"),
    )
    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        db.execute("INSERT INTO health_state (key, value) VALUES ('alert:sc_login', '1')")
    (alarm,) = collect_status(_cfg(tmp_path), now=NOW).alerts
    assert geheim not in (alarm.error or "")


def test_fehlende_tabelle_liefert_leere_liste(tmp_path):
    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        db.execute("CREATE TABLE andere (x)")
    assert _read_active_alerts(tmp_path / "seen.sqlite") == []


def test_alarm_ohne_runs_tabelle_hat_leere_felder(tmp_path):
    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        db.execute("CREATE TABLE health_state (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO health_state VALUES ('alert:disk', '1')")
    (alarm,) = _read_active_alerts(tmp_path / "seen.sqlite")
    assert (alarm.mode, alarm.since, alarm.error) == ("disk", None, None)


def test_statusseite_ohne_fehlertext(tmp_path):
    _seed(tmp_path, "INSERT INTO health_state (key, value) VALUES ('alert:disk', '1')")
    text = TestClient(create_app(_cfg(tmp_path), password="pw-alarme")).get("/", auth=AUTH).text
    assert "Speicherplatz: ohne Fehlertext" in text
    assert "Ein Health-Alarm ist aktiv" not in text
