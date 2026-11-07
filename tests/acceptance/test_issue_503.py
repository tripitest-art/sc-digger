"""Akzeptanztests: Statusseite listet alle aktiven Health-Alarme."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.health import Health
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.status import ALERT_LABELS, AlertInfo, collect_status

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
AUTH = ("x", "geheim-alarme")


def make_cfg(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def seed(tmp_path, runs=(), alerts=()):
    """runs: (mode, finished_at, ok, found, error); alerts: (mode, wert)."""
    Health(tmp_path / "seen.sqlite").db.close()  # legt runs und health_state an
    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        for mode, finished_at, ok, found, error in runs:
            db.execute(
                "INSERT INTO runs (mode, finished_at, ok, found, error) VALUES (?,?,?,?,?)",
                (mode, finished_at, ok, found, error),
            )
        for mode, wert in alerts:
            db.execute("INSERT INTO health_state (key, value) VALUES (?, ?)", (f"alert:{mode}", wert))


def test_ohne_alarme_leere_liste(tmp_path):
    seed(tmp_path)
    assert collect_status(make_cfg(tmp_path), now=NOW).alerts == []


def test_fehlende_datenbank_liefert_leere_liste_ohne_neue_datei(tmp_path):
    snap = collect_status(make_cfg(tmp_path), now=NOW)
    assert snap.alerts == []
    assert not (tmp_path / "seen.sqlite").exists()


def test_alarm_eines_anderen_modus_wird_gelistet(tmp_path):
    seed(
        tmp_path,
        runs=[
            ("sc_login", "2026-11-05 07:30:00", 0, 0, "SOUNDCLOUD_AUTH_TOKEN ungültig"),
            ("sc_login", "2026-11-06 07:30:00", 0, 0, "SOUNDCLOUD_AUTH_TOKEN abgelaufen"),
        ],
        alerts=[("sc_login", "1")],
    )
    snap = collect_status(make_cfg(tmp_path), now=NOW)
    assert snap.alerts == [
        AlertInfo(
            mode="sc_login",
            label="SoundCloud-Login",
            since="2026-11-06 07:30:00",
            error="SOUNDCLOUD_AUTH_TOKEN abgelaufen",
        )
    ]
    assert snap.alert_active is False


def test_entwarnung_wird_nicht_gelistet(tmp_path):
    seed(tmp_path, alerts=[("sc_login", "0"), ("disk", "0")])
    assert collect_status(make_cfg(tmp_path), now=NOW).alerts == []


def test_reihenfolge_bekannte_modi_zuerst_dann_unbekannte(tmp_path):
    seed(tmp_path, alerts=[("zebra", "1"), ("sc_login", "1"), ("discover", "1"), ("apfel", "1")])
    modes = [a.mode for a in collect_status(make_cfg(tmp_path), now=NOW).alerts]
    assert modes == ["discover", "sc_login", "apfel", "zebra"]
    labels = {a.mode: a.label for a in collect_status(make_cfg(tmp_path), now=NOW).alerts}
    assert labels["discover"] == ALERT_LABELS["discover"] == "Täglicher Lauf"
    assert labels["zebra"] == "zebra"


def test_alarm_ohne_schlechten_lauf_hat_leere_felder(tmp_path):
    seed(tmp_path, runs=[("disk", "2026-11-06 07:00:00", 1, 1, None)], alerts=[("disk", "1")])
    (alarm,) = collect_status(make_cfg(tmp_path), now=NOW).alerts
    assert (alarm.mode, alarm.since, alarm.error) == ("disk", None, None)


def test_found_null_zaehlt_als_schlechter_lauf(tmp_path):
    seed(tmp_path, runs=[("discover", "2026-11-06 07:30:00", 1, 0, None)], alerts=[("discover", "1")])
    (alarm,) = collect_status(make_cfg(tmp_path), now=NOW).alerts
    assert alarm.since == "2026-11-06 07:30:00"
    assert alarm.error is None


def test_snapshot_ist_json_faehig_und_enthaelt_alarme(tmp_path):
    seed(tmp_path, runs=[("disk", "2026-11-06 07:00:00", 0, 0, "Speicher knapp")], alerts=[("disk", "1")])
    data = collect_status(make_cfg(tmp_path), now=NOW).to_dict()
    json.dumps(data)
    assert data["alerts"] == [
        {"mode": "disk", "label": "Speicherplatz", "since": "2026-11-06 07:00:00", "error": "Speicher knapp"}
    ]


def test_statusseite_zeigt_alarmliste(tmp_path):
    seed(
        tmp_path,
        runs=[("sc_login", "2026-11-06 07:30:00", 0, 0, "Token <b>abgelaufen</b>")],
        alerts=[("sc_login", "1")],
    )
    client = TestClient(create_app(make_cfg(tmp_path), password="geheim-alarme"))
    text = client.get("/", auth=AUTH).text
    assert 'id="health-alerts"' in text
    assert 'data-mode="sc_login"' in text
    assert "SoundCloud-Login" in text
    assert "seit 2026-11-06 07:30:00 UTC" in text
    assert "<b>abgelaufen</b>" not in text


def test_statusseite_ohne_alarme_hat_keinen_block(tmp_path):
    seed(tmp_path)
    client = TestClient(create_app(make_cfg(tmp_path), password="geheim-alarme"))
    assert 'id="health-alerts"' not in client.get("/", auth=AUTH).text
