"""Akzeptanztests: Die Statusseite misst den Speicher nicht am Testrechner."""

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
GB = 1024 ** 3
PASSWORD = "test-pw-ops"
AUTH = ("sc", PASSWORD)


def _cfg(tmp_path: Path, min_free_gb: float = 10) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["download"] = {
        **cfg.raw.get("download", {}),
        "inbox_dir": str(tmp_path / "inbox"),
        "min_free_gb": min_free_gb,
    }
    cfg.raw["backup"] = {"target_dir": ""}
    cfg.raw["state"] = {
        **cfg.raw.get("state", {}),
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def _usage(freie_gb: float):
    return lambda path: SimpleNamespace(total=100 * GB, used=(100 - freie_gb) * GB, free=freie_gb * GB)


def test_statusseite_nutzt_die_uebergebene_messung(tmp_path):
    client = TestClient(create_app(_cfg(tmp_path), password=PASSWORD, disk_usage=_usage(50)))
    html = client.get("/", auth=AUTH).text
    assert "Betrieb" in html
    assert "50.0 GB frei" in html


def test_wenig_platz_zeigt_warnung_ohne_echten_datentraeger(tmp_path):
    client = TestClient(create_app(_cfg(tmp_path), password=PASSWORD, disk_usage=_usage(1)))
    html = client.get("/", auth=AUTH).text
    assert "Nur 1.0 GB frei" in html
    assert "50.0 GB frei" not in html
