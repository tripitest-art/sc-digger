"""Unit-Tests für die Sammlungs-Audit-Seite (Issue #657)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.audit_page import read_last_report

PASSWORD = "audit-unit-test-password"
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


def test_read_last_report_existing_file(tmp_path: Path):
    report_file = tmp_path / "audit-report.html"
    report_file.write_text("<html>Report</html>", encoding="utf-8")

    info = read_last_report(report_file)
    assert info is not None
    assert info["path"] == str(report_file)
    assert info["size"] == len("<html>Report</html>".encode("utf-8"))
    assert "T" in info["written_at"]


def test_read_last_report_missing_and_directory(tmp_path: Path):
    assert read_last_report(tmp_path / "nonexistent.html") is None
    assert read_last_report(tmp_path) is None


def test_audit_page_renders_with_and_without_report(tmp_path: Path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg, password=PASSWORD)

    with TestClient(app) as client:
        # 1. Ohne Bericht
        r1 = client.get("/audit", auth=AUTH)
        assert r1.status_code == 200
        assert "Sammlungs-Audit" in r1.text
        assert "check-adopted" in r1.text
        assert "write-tags" in r1.text
        assert "export-xml" in r1.text
        assert "analyze-structure" in r1.text
        assert "Noch kein Audit-Bericht vorhanden" in r1.text

        # 2. Mit Bericht
        report_file = tmp_path / "audit-report.html"
        report_file.write_text("<html>Bericht</html>", encoding="utf-8")

        r2 = client.get("/audit", auth=AUTH)
        assert r2.status_code == 200
        assert "Bericht anzeigen" in r2.text
        assert str(report_file) in r2.text


def test_api_audit_report_redirect_and_404(tmp_path: Path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg, password=PASSWORD)

    with TestClient(app) as client:
        # Ohne Bericht -> 404
        r1 = client.get("/api/audit/report", auth=AUTH)
        assert r1.status_code == 404

        # Mit Bericht -> 307 Weiterleitung
        report_file = tmp_path / "audit-report.html"
        report_file.write_text("<html>Bericht</html>", encoding="utf-8")

        r2 = client.get("/api/audit/report", auth=AUTH, follow_redirects=False)
        assert r2.status_code == 307
        assert r2.headers["location"] == "/audit-report.html"


def test_audit_page_requires_authentication(tmp_path: Path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg, password=PASSWORD)

    with TestClient(app) as client:
        assert client.get("/audit").status_code == 401
        assert client.get("/api/audit/report").status_code == 401


def test_default_runner_audit(tmp_path: Path):
    from sc_digger.web.tasks import action_labels, default_runners

    cfg = _cfg(tmp_path)
    runners = default_runners(cfg)
    assert "audit" in runners
    assert "audit" in action_labels()

    summary_mock = MagicMock()
    summary_mock.scanned = 42
    summary_mock.ok = 40
    summary_mock.adopted_tracks = [("/inbox/a.mp3", "/archive/a.mp3")]

    with patch("sc_digger.audit.run_audit", return_value=summary_mock) as mock_run:
        result = runners["audit"]()
        assert "42 gescannt" in result
        assert "40 ok" in result
        assert "1 übernommen" in result
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["check_adopted"] is True
        assert kwargs["no_telegram"] is True
        assert kwargs["report_path"] == tmp_path / "audit-report.html"
