"""Akzeptanztests: Verlauf der Konfigänderungen mit Rückgängig (Issue #697)."""
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web import app as web_app
from sc_digger.web.configedit import list_backups, restore_backup, save_local

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-verlauf-4711"
AUTH = ("sc", PASSWORD)
ORIGIN = {"origin": "http://testserver"}


def _pfad(tmp_path):
    return tmp_path / "config.local.yaml"


def test_sicherungen_werden_neueste_zuerst_gelistet(tmp_path):
    pfad = _pfad(tmp_path)
    # save_local sichert nur die Vorfassung einer bereits vorhandenen Datei; ohne Startstand
    # gaebe es nach zwei Speicherungen nur eine Sicherung.
    pfad.write_text("web:\n  a: 0\n", encoding="utf-8")
    save_local(pfad, {"web": {"a": 1}}, now=datetime(2026, 1, 1, 12, 0, 0))
    save_local(pfad, {"web": {"a": 2}}, now=datetime(2026, 1, 1, 12, 0, 1))
    eintraege = list_backups(pfad)
    assert len(eintraege) == 2
    assert eintraege[0]["name"].endswith("120001")
    assert eintraege[1]["name"].endswith("120000")


def test_wiederherstellen_holt_den_alten_stand_zurueck(tmp_path):
    pfad = _pfad(tmp_path)
    pfad.write_text("web:\n  a: 0\n", encoding="utf-8")
    save_local(pfad, {"web": {"a": 1}}, now=datetime(2026, 1, 1, 12, 0, 0))
    save_local(pfad, {"web": {"a": 2}}, now=datetime(2026, 1, 1, 12, 0, 1))
    # Neueste Sicherung = Stand vor der letzten Speicherung (a: 1).
    vorher = list_backups(pfad)[0]["name"]
    neu = restore_backup(pfad, vorher)
    assert neu is not None and Path(neu).exists()
    assert "a: 1" in pfad.read_text(encoding="utf-8")


def test_unbekannter_name_wird_abgelehnt(tmp_path):
    pfad = _pfad(tmp_path)
    save_local(pfad, {"web": {"a": 1}}, now=datetime(2026, 1, 1, 12, 0, 0))
    save_local(pfad, {"web": {"a": 2}}, now=datetime(2026, 1, 1, 12, 0, 1))
    assert restore_backup(pfad, "../config.local.yaml") is None
    assert restore_backup(pfad, "config.local.yaml.bak-19000101-000000") is None


def test_route_lehnt_pfadanteile_ab(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
        "health_db_path": str(tmp_path / "seen.sqlite"),
    }
    # Die Editor-Routen existieren nur mit base_path UND local_path (wie im Betrieb über /config).
    client = TestClient(web_app.create_app(
        cfg, password=PASSWORD,
        base_path=ROOT / "config.yaml",
        local_path=tmp_path / "config.local.yaml",
    ))
    antwort = client.post(
        "/api/config/restore",
        json={"backup": "../../etc/passwd"},
        headers=ORIGIN,
        auth=AUTH,
    )
    assert antwort.status_code == 400
    assert "Ungültiger Sicherungsname" in antwort.json()["error"]
