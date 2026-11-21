"""Akzeptanztests: Tagesablauf-Karte mit Zeitplan und letztem Ergebnis (Issue #680)."""
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.health import Health
from sc_digger.models import Config
from sc_digger.schedule import JOBS
from sc_digger.web import app as web_app

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-ablauf-4711"
AUTH = ("sc", PASSWORD)


def _cfg(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
        "health_db_path": str(tmp_path / "health.sqlite"),
    }
    cfg.raw["download"] = {
        "inbox_dir": str(tmp_path / "inbox"),
        "collection_dir": str(tmp_path / "collection"),
    }
    return cfg


def test_zeitplan_ist_sortiert_und_eindeutig():
    assert [job.uhrzeit for job in JOBS] == sorted(job.uhrzeit for job in JOBS)
    namen = [job.name for job in JOBS]
    assert len(namen) == len(set(namen))
    assert {"backup", "audit", "taste", "canary", "discover", "scout"} <= set(namen)


def test_jeder_job_steht_in_der_crontab():
    # Zwei Quellen: entrypoint.sh ergänzt die Crontab zur Laufzeit, der Discover-Job steht im Dockerfile.
    quellen = (ROOT / "entrypoint.sh").read_text(encoding="utf-8") + (ROOT / "Dockerfile").read_text(encoding="utf-8")
    for job in JOBS:
        assert job.cron in quellen, f"{job.name}: Zeit {job.cron} fehlt in entrypoint.sh/Dockerfile"
        assert job.kennung in quellen, f"{job.name}: {job.kennung} fehlt in entrypoint.sh/Dockerfile"


def test_karte_zeigt_uhrzeit_und_letztes_ergebnis(tmp_path):
    cfg = _cfg(tmp_path)
    with Health(cfg["state"]["health_db_path"]) as health:
        health.record("discover", ok=True, found=12)
    client = TestClient(web_app.create_app(cfg, password=PASSWORD))
    html = client.get("/", auth=AUTH).text
    assert "Tagesablauf" in html
    assert "06:30" in html
    assert "12 gefunden" in html


def test_ohne_lauf_steht_noch_kein_lauf(tmp_path):
    cfg = _cfg(tmp_path)
    client = TestClient(web_app.create_app(cfg, password=PASSWORD))
    html = client.get("/", auth=AUTH).text
    assert "Tagesablauf" in html
    assert "noch kein Lauf" in html
