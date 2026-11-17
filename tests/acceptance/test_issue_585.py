"""Akzeptanztests: Alarm-Zähler im Kopf jeder Seite."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from starlette.testclient import TestClient

from sc_digger.health import Health
from sc_digger.models import Config
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 17, 6, 0, tzinfo=timezone.utc)
PASSWORD = "geheim-alarm"
AUTH = ("sc", PASSWORD)


def _cfg(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def _client(tmp_path):
    return TestClient(create_app(_cfg(tmp_path), password=PASSWORD))


def _seed_alarm(tmp_path, mode: str):
    Health(tmp_path / "seen.sqlite").db.close()
    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        db.execute(
            "INSERT INTO runs (mode, finished_at, ok, found, error) VALUES (?, ?, 0, 0, ?)",
            (mode, "2026-11-17 06:00:00", "fehlgeschlagen"),
        )
        db.execute("INSERT INTO health_state (key, value) VALUES (?, '1')", (f"alert:{mode}",))


def _html(client, pfad: str) -> str:
    antwort = client.get(pfad, auth=AUTH)
    assert antwort.status_code == 200, (pfad, antwort.status_code)
    return antwort.text


def test_ohne_alarm_kein_badge(tmp_path):
    html = _html(_client(tmp_path), "/")
    assert 'id="alarm-badge"' not in html


def test_startseite_zeigt_den_badge_mit_anzahl(tmp_path):
    _seed_alarm(tmp_path, "discover")
    html = _html(_client(tmp_path), "/")
    assert 'id="alarm-badge"' in html
    assert 'href="/status"' in html


def test_jede_seite_zeigt_den_badge(tmp_path):
    _seed_alarm(tmp_path, "discover")
    client = _client(tmp_path)
    for pfad in ("/", "/tracks", "/digest", "/kaufliste"):
        assert 'id="alarm-badge"' in _html(client, pfad), pfad


def test_zwei_alarme_ergeben_zwei(tmp_path):
    _seed_alarm(tmp_path, "discover")
    _seed_alarm(tmp_path, "watch")
    html = _html(_client(tmp_path), "/")
    assert "2 Alarme" in html
