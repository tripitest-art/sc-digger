"""Tests für Konfigurationsverlauf und Wiederherstellen (Issue #689)."""
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web import app as web_app
from sc_digger.web.configedit import list_backups, restore_backup, save_local

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "test-verlauf-password"
AUTH = ("sc", PASSWORD)
ORIGIN = {"origin": "http://testserver"}


def test_list_backups_respektiert_limit_und_sortierung(tmp_path: Path):
    local_file = tmp_path / "config.local.yaml"
    # Erstes save_local legt die Datei an (noch keine Vorfassung, kein Backup).
    # Weitere 10 save_local sichern die jeweilige Vorfassung.
    for i in range(11):
        save_local(local_file, {"web": {"idx": i}}, now=datetime(2026, 1, 1, 10, 0, i))

    # Insgesamt 10 Backups vorhanden (keep_backups=10 Standard)
    alle = list_backups(local_file, limit=100)
    assert len(alle) == 10
    assert alle[0]["name"].endswith("100010")
    assert alle[-1]["name"].endswith("100001")

    # Limit greift
    begrenzt = list_backups(local_file, limit=3)
    assert len(begrenzt) == 3
    assert [b["name"] for b in begrenzt] == [b["name"] for b in alle[:3]]

    # Nicht existentes Verzeichnis / leere Sicherungen
    assert list_backups(tmp_path / "nicht_da" / "config.local.yaml") == []


def test_verlauf_seite_listet_namen_und_behandelt_leeren_zustand(tmp_path: Path):
    base_file = ROOT / "config.yaml"
    local_file = tmp_path / "config.local.yaml"

    cfg = Config.load(base_file)
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
        "health_db_path": str(tmp_path / "seen.sqlite"),
    }

    client = TestClient(web_app.create_app(cfg, password=PASSWORD, base_path=base_file, local_path=local_file))

    # 1. Ohne Backups -> Text "keine Sicherungen vorhanden"
    r1 = client.get("/config/verlauf", auth=AUTH)
    assert r1.status_code == 200
    assert "id=\"config-verlauf\"" in r1.text
    assert "keine Sicherungen vorhanden" in r1.text

    # 2. Mit Backups -> Namen werden aufgelistet
    # Erste Version schreiben (kein Backup), dann 2 Versionen (2 Backups: v:0 und v:1)
    save_local(local_file, {"web": {"v": 0}}, now=datetime(2026, 2, 1, 11, 0, 0))
    save_local(local_file, {"web": {"v": 1}}, now=datetime(2026, 2, 1, 12, 0, 0))
    save_local(local_file, {"web": {"v": 2}}, now=datetime(2026, 2, 1, 12, 0, 1))

    r2 = client.get("/config/verlauf", auth=AUTH)
    assert r2.status_code == 200
    assert "id=\"config-verlauf\"" in r2.text
    backups = list_backups(local_file)
    assert len(backups) == 2
    for b in backups:
        assert b["name"] in r2.text


def test_restore_route_stellt_stand_wieder_her_und_aktualisiert_app_state(tmp_path: Path):
    base_file = ROOT / "config.yaml"
    local_file = tmp_path / "config.local.yaml"

    cfg = Config.load(base_file)
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
        "health_db_path": str(tmp_path / "seen.sqlite"),
    }

    app = web_app.create_app(cfg, password=PASSWORD, base_path=base_file, local_path=local_file)
    client = TestClient(app)

    # Datei anlegen und zwei Änderungen speichern:
    # 1. initial: {"web": {"v": 111}}
    # 2. Änderung: {"web": {"v": 222}} -> sichert die Vorfassung {"web": {"v": 111}}
    save_local(local_file, {"web": {"v": 111}}, now=datetime(2026, 3, 1, 14, 0, 0))
    save_local(local_file, {"web": {"v": 222}}, now=datetime(2026, 3, 1, 14, 0, 5))

    backups = list_backups(local_file)
    assert len(backups) == 1
    gesicherte_version = backups[0]["name"]

    # Restore über POST /api/config/restore
    resp = client.post(
        "/api/config/restore",
        json={"backup": gesicherte_version},
        headers=ORIGIN,
        auth=AUTH,
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "backup": gesicherte_version}

    # Dateiinhalt muss nun v: 111 sein
    assert "v: 111" in local_file.read_text(encoding="utf-8")

    # Durch das Wiederherstellen wurde vorher der Stand 2 (v: 222) als weiteres Backup gesichert
    neue_backups = list_backups(local_file)
    assert len(neue_backups) == 2


def test_restore_route_validierung_und_fehlerfaelle(tmp_path: Path):
    base_file = ROOT / "config.yaml"
    local_file = tmp_path / "config.local.yaml"

    cfg = Config.load(base_file)
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
        "health_db_path": str(tmp_path / "seen.sqlite"),
    }

    client = TestClient(web_app.create_app(cfg, password=PASSWORD, base_path=base_file, local_path=local_file))

    # Origin Check
    r_origin = client.post("/api/config/restore", json={"backup": "test"}, headers={"origin": "http://evil.com"}, auth=AUTH)
    assert r_origin.status_code == 403

    # Ungültiges JSON / Payload
    r_bad = client.post("/api/config/restore", content="nicht-json", headers={"content-type": "application/json", **ORIGIN}, auth=AUTH)
    assert r_bad.status_code == 400

    # Unbekannter Sicherungsname
    r_unknown = client.post("/api/config/restore", json={"backup": "config.local.yaml.bak-20999999-000000"}, headers=ORIGIN, auth=AUTH)
    assert r_unknown.status_code == 400
    assert "Ungültiger Sicherungsname" in r_unknown.json()["error"]
