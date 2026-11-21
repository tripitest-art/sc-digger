"""Akzeptanztests für die Sammlungs-Audit-Seite (Issue #657)."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app

PASSWORD = "audit-test-password"
AUTH = ("sc-digger", PASSWORD)


def _cfg(tmp_path: Path) -> Config:
    return Config(raw={
        "state": {
            "db_path": str(tmp_path / "seen.sqlite"),
            "track_db_path": str(tmp_path / "tracks.sqlite"),
            "health_db_path": str(tmp_path / "seen.sqlite"),
        },
        "download": {
            "inbox_dir": str(tmp_path / "inbox"),
            "collection_dir": str(tmp_path / "collection"),
        },
    })


def test_audit_seite_erklaert_die_optionen(tmp_path: Path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg, password=PASSWORD, maintenance_runners={"audit": lambda: "fertig"})

    with TestClient(app) as client:
        seite = client.get("/audit", auth=AUTH)
        assert seite.status_code == 200
        assert "Sammlungs-Audit" in seite.text
        assert "check-adopted" in seite.text


def test_audit_startet_ueber_die_wartung(tmp_path: Path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg, password=PASSWORD, maintenance_runners={"audit": lambda: "fertig"})

    with TestClient(app) as client:
        antwort = client.post("/api/maintenance/audit", auth=AUTH)

    assert antwort.status_code == 202
    assert antwort.json()["action"] == "audit"


def test_abgelaufener_bericht_wird_als_fehlt_gemeldet(tmp_path: Path):
    from sc_digger.web.audit_page import read_last_report

    assert read_last_report(tmp_path / "fehlt.html") is None
