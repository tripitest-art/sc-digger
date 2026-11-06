"""Eigene Tests zum Vorperioden-Vergleich der Statusseite (Issue #488)."""
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.status import change_badge

ROOT = Path(__file__).resolve().parents[1]


def test_change_badge_ohne_vorperiode():
    assert change_badge(None, 5) == {"direction": "none", "text": "–"}
    assert change_badge(-3, 5) == {"direction": "none", "text": "–"}


def test_change_badge_gleichstand_ist_flach():
    assert change_badge(7, 7) == {"direction": "flat", "text": "● 0 %"}


def test_change_badge_auf_und_ab():
    assert change_badge(100, 112) == {"direction": "up", "text": "▲ +12 %"}
    assert change_badge(100, 75) == {"direction": "down", "text": "▼ −25 %"}


def test_change_badge_rundet_kaufmaennisch_auf_ganze_prozent():
    assert change_badge(3, 4)["text"] == "▲ +33 %"
    assert change_badge(200, 1)["text"] == "▼ −100 %"


def test_change_badge_arbeitet_mit_kommazahlen():
    assert change_badge(2.0, 3.0) == {"direction": "up", "text": "▲ +50 %"}


def test_statusseite_zeigt_badges_mit_titel(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    client = TestClient(create_app(cfg, password="geheim-488"))
    r = client.get("/", auth=("sc", "geheim-488"))
    assert r.status_code == 200
    # Ohne Vorperioden-Daten steht an jeder der vier Kennzahlen das neutrale Badge.
    assert r.text.count("change-badge") >= 4
    assert "change-none" in r.text
    assert "Vorperiode:" in r.text
