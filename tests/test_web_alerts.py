"""Tests für Health-Alarme auf der Statusseite (Issue #503)."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.status import (
    ALERT_LABELS,
    AlertInfo,
    _read_active_alerts,
    collect_status,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
AUTH = ("x", "geheim-test")


def make_cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def test_alert_info_dataclass():
    info = AlertInfo(mode="disk", label="Speicherplatz", since="2026-11-06 10:00:00", error="Nur noch 1 GB frei")
    assert info.mode == "disk"
    assert info.label == "Speicherplatz"
    assert info.since == "2026-11-06 10:00:00"
    assert info.error == "Nur noch 1 GB frei"


def test_alert_info_defaults():
    info = AlertInfo(mode="disk", label="Speicherplatz")
    assert info.since is None
    assert info.error is None


def test_read_active_alerts_nonexistent_db(tmp_path: Path):
    non_existent = tmp_path / "does_not_exist.sqlite"
    alerts = _read_active_alerts(non_existent)
    assert alerts == []
    assert not non_existent.exists()


def test_read_active_alerts_missing_tables(tmp_path: Path):
    db_path = tmp_path / "empty.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE dummy (id INTEGER PRIMARY KEY)")
    alerts = _read_active_alerts(db_path)
    assert alerts == []


def test_read_active_alerts_missing_runs_table(tmp_path: Path):
    db_path = tmp_path / "no_runs.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE health_state (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO health_state VALUES ('alert:sc_login', '1')")
    alerts = _read_active_alerts(db_path)
    assert len(alerts) == 1
    assert alerts[0] == AlertInfo(
        mode="sc_login",
        label="SoundCloud-Login",
        since=None,
        error=None,
    )


def test_read_active_alerts_sorting_and_labels(tmp_path: Path):
    db_path = tmp_path / "seen.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE health_state (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT, finished_at TEXT, ok INTEGER, found INTEGER, error TEXT)")
        # 3 bekannte Modi in umgekehrter Reihenfolge und 2 unbekannte
        for m in ("rekordbox_import", "sc_login", "discover", "zeta_custom", "beta_custom"):
            db.execute("INSERT INTO health_state VALUES (?, '1')", (f"alert:{m}",))

    alerts = _read_active_alerts(db_path)
    modes = [a.mode for a in alerts]
    # Bekannte Modi in ALERT_LABELS-Reihenfolge: discover, sc_login, rekordbox_import
    # Unbekannte Modi alphabetisch: beta_custom, zeta_custom
    assert modes == ["discover", "sc_login", "rekordbox_import", "beta_custom", "zeta_custom"]
    assert alerts[0].label == ALERT_LABELS["discover"]
    assert alerts[1].label == ALERT_LABELS["sc_login"]
    assert alerts[2].label == ALERT_LABELS["rekordbox_import"]
    assert alerts[3].label == "beta_custom"
    assert alerts[4].label == "zeta_custom"


def test_read_active_alerts_redacts_error(tmp_path: Path):
    db_path = tmp_path / "seen.sqlite"
    secret_url = "https://api.soundcloud.com/tracks?oauth_token=0123456789abcdef0123456789abcdef"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE health_state (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT, finished_at TEXT, ok INTEGER, found INTEGER, error TEXT)")
        db.execute("INSERT INTO health_state VALUES ('alert:discover', '1')")
        db.execute(
            "INSERT INTO runs (mode, finished_at, ok, found, error) VALUES (?, ?, ?, ?, ?)",
            ("discover", "2026-11-06 08:00:00", 0, 0, f"Fehler bei Aufruf von {secret_url}"),
        )

    alerts = _read_active_alerts(db_path)
    assert len(alerts) == 1
    assert "0123456789abcdef" not in (alerts[0].error or "")
    assert "***" in (alerts[0].error or "")


def test_read_active_alerts_picks_latest_bad_run(tmp_path: Path):
    db_path = tmp_path / "seen.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE health_state (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT, finished_at TEXT, ok INTEGER, found INTEGER, error TEXT)")
        db.execute("INSERT INTO health_state VALUES ('alert:disk', '1')")
        db.execute("INSERT INTO runs (mode, finished_at, ok, found, error) VALUES (?, ?, ?, ?, ?)", ("disk", "2026-11-05 06:00:00", 0, 0, "Alter Fehler"))
        db.execute("INSERT INTO runs (mode, finished_at, ok, found, error) VALUES (?, ?, ?, ?, ?)", ("disk", "2026-11-06 06:00:00", 0, 0, "Neuer Fehler"))
        # Auch ein neuerer erfolgreicher Lauf überschreibt nicht den schlechten Lauf solange der Alarm aktiv ist
        db.execute("INSERT INTO runs (mode, finished_at, ok, found, error) VALUES (?, ?, ?, ?, ?)", ("disk", "2026-11-06 07:00:00", 1, 1, None))

    alerts = _read_active_alerts(db_path)
    assert len(alerts) == 1
    assert alerts[0].since == "2026-11-06 06:00:00"
    assert alerts[0].error == "Neuer Fehler"


def test_html_renders_multiple_alerts_and_escaping(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    state_db = tmp_path / "seen.sqlite"
    with sqlite3.connect(state_db) as db:
        db.execute("CREATE TABLE health_state (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT, finished_at TEXT, ok INTEGER, found INTEGER, error TEXT)")
        db.execute("INSERT INTO health_state VALUES ('alert:sc_login', '1')")
        db.execute("INSERT INTO health_state VALUES ('alert:disk', '1')")
        db.execute(
            "INSERT INTO runs (mode, finished_at, ok, found, error) VALUES (?, ?, ?, ?, ?)",
            ("sc_login", "2026-11-06 07:30:00", 0, 0, "<script>alert('xss')</script>"),
        )
        # disk hat keinen Fehlertext
        db.execute(
            "INSERT INTO runs (mode, finished_at, ok, found, error) VALUES (?, ?, ?, ?, ?)",
            ("disk", "2026-11-06 08:00:00", 1, 0, None),
        )

    app = create_app(cfg, password="geheim-test")
    client = TestClient(app)
    response = client.get("/", auth=AUTH)
    assert response.status_code == 200
    html = response.text

    assert 'id="health-alerts"' in html
    assert 'role="alert"' in html
    assert 'data-mode="sc_login"' in html
    assert 'data-mode="disk"' in html
    assert "SoundCloud-Login" in html
    assert "Speicherplatz" in html
    assert "ohne Fehlertext" in html
    # XSS-Schutz: HTML-Tags müssen im Fehlertext escaped sein
    assert "<script>alert('xss')</script>" not in html
    assert "&lt;script&gt;alert(&#39;xss&#39;)&lt;/script&gt;" in html or "&lt;script&gt;alert('xss')&lt;/script&gt;" in html
