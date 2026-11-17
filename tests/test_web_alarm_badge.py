"""Alarm-Zähler im Kopf jeder Seite (Issue #585)."""
import re
import sqlite3
from pathlib import Path

from starlette.testclient import TestClient

from sc_digger.health import Health
from sc_digger.models import Config
from sc_digger.web.app import active_alarm_count, create_app
from sc_digger.web.status import collect_status

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-badge"
AUTH = ("sc", PASSWORD)
BADGE_RE = re.compile(r'<a href="/status" id="alarm-badge"[^>]*>\s*⚠ (\d+) (Alarme?)\s*</a>')


def _cfg(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def _client(tmp_path):
    return TestClient(create_app(_cfg(tmp_path), password=PASSWORD))


def _seed(tmp_path, key: str, value: str = "1", mode: str | None = None):
    Health(tmp_path / "seen.sqlite").db.close()
    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        if mode:
            db.execute(
                "INSERT INTO runs (mode, finished_at, ok, found, error) VALUES (?, ?, 0, 0, ?)",
                (mode, "2026-11-17 06:00:00", "kaputt"),
            )
        db.execute("INSERT INTO health_state (key, value) VALUES (?, ?)", (key, value))


def _badge(html: str):
    m = BADGE_RE.search(html)
    return (int(m.group(1)), m.group(2)) if m else None


def test_zwei_alarme_zahl_im_badge_auf_mehreren_seiten(tmp_path):
    _seed(tmp_path, "alert:discover", mode="discover")
    _seed(tmp_path, "alert:watch", mode="watch")
    client = _client(tmp_path)
    for pfad in ("/", "/tracks", "/digest", "/kaufliste", "/rejected", "/mix", "/intake"):
        antwort = client.get(pfad, auth=AUTH)
        assert antwort.status_code == 200, pfad
        assert _badge(antwort.text) == (2, "Alarme"), pfad


def test_ein_alarm_im_singular(tmp_path):
    _seed(tmp_path, "alert:discover", mode="discover")
    html = _client(tmp_path).get("/", auth=AUTH).text
    assert _badge(html) == (1, "Alarm")


def test_ohne_datenbank_kein_badge_und_keine_datei(tmp_path):
    # Seite ohne jede Daten: rendert, zeigt keinen Badge und legt keine State-DB an.
    client = _client(tmp_path)
    for pfad in ("/", "/tracks", "/digest"):
        antwort = client.get(pfad, auth=AUTH)
        assert antwort.status_code == 200, pfad
        assert 'id="alarm-badge"' not in antwort.text, pfad
    assert not (tmp_path / "seen.sqlite").exists()


def test_quittierte_und_fremde_schluessel_zaehlen_nicht(tmp_path):
    _seed(tmp_path, "alert:discover", value="0", mode="discover")
    _seed(tmp_path, "last_digest", value="1")
    html = _client(tmp_path).get("/", auth=AUTH).text
    assert 'id="alarm-badge"' not in html


def test_badge_und_statusseite_zeigen_dieselbe_zahl(tmp_path):
    _seed(tmp_path, "alert:discover", mode="discover")
    _seed(tmp_path, "alert:unbekannt")  # Alarm ohne passenden Lauf zählt trotzdem mit
    cfg = _cfg(tmp_path)
    html = TestClient(create_app(cfg, password=PASSWORD)).get("/", auth=AUTH).text
    assert _badge(html)[0] == len(collect_status(cfg).alerts) == 2


def test_json_antworten_ohne_badge(tmp_path):
    _seed(tmp_path, "alert:discover", mode="discover")
    antwort = _client(tmp_path).get("/healthz", auth=AUTH)
    assert "alarm-badge" not in antwort.text


def test_ohne_anfrage_kontext_null():
    assert active_alarm_count() == 0
