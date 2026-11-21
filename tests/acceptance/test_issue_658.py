"""Akzeptanztests für die Statistik-Seite mit Trend-Radar (Issue #658)."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.statistik import collect_statistics

PASSWORD = "statistik-test-password"
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


def test_collect_statistics_ohne_datenbanken(tmp_path: Path):
    daten = collect_statistics(
        tmp_path / "tracks.sqlite",
        tmp_path / "seen.sqlite",
        days=30,
    )

    assert daten["days"] == 30
    assert daten["funnel"]["runs_total"] == 0
    assert daten["trends"] == {"artists": [], "tracks": [], "tags": []}


def test_collect_statistics_lehnt_leeres_fenster_ab(tmp_path: Path):
    import pytest

    from sc_digger.web.statistik import collect_statistics as sammeln

    with pytest.raises(ValueError):
        sammeln(tmp_path / "tracks.sqlite", tmp_path / "seen.sqlite", days=0)


def test_seite_und_api_geben_das_fenster_zurueck(tmp_path: Path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg, password=PASSWORD)

    with TestClient(app) as client:
        seite = client.get("/statistik?days=30", auth=AUTH)
        assert seite.status_code == 200
        assert "30 Tage" in seite.text

        api = client.get("/api/statistik?days=30", auth=AUTH)
        assert api.status_code == 200
        assert api.json()["days"] == 30

        assert client.get("/api/statistik?days=0", auth=AUTH).status_code == 400
