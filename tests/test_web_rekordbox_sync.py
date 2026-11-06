"""Eigene Tests zum Rekordbox-XML-Button im Dashboard (Issue #495)."""
import xml.etree.ElementTree as ET
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web import app as webapp
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-xml-eigene"
AUTH = ("x", PASSWORD)


def make_cfg(tmp_path, *, enabled=True):
    cfg = Config.load(ROOT / "config.yaml")
    inbox = tmp_path / "inbox"
    collection = tmp_path / "sammlung"
    inbox.mkdir()
    collection.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    cfg.raw["download"] = {
        **cfg.raw.get("download", {}),
        "inbox_dir": str(inbox),
        "collection_dir": str(collection),
    }
    cfg.raw["rekordbox"] = {
        "xml_enabled": enabled,
        "xml_path": str(out / "sc-digger.xml"),
        "path_map": {},
    }
    return cfg


def client_for(cfg):
    return TestClient(create_app(cfg, password=PASSWORD))


def test_entries_werden_aus_geschriebener_xml_gelesen(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    ziel = tmp_path / "out" / "sc-digger.xml"

    def fake_write(_cfg):
        root = ET.Element("DJ_PLAYLISTS")
        ET.SubElement(root, "COLLECTION", {"Entries": "3"})
        ziel.write_bytes(ET.tostring(root))
        return ziel

    monkeypatch.setattr(webapp.rekordbox, "write_rekordbox_xml", fake_write)
    status, body = webapp.sync_rekordbox_xml(cfg)
    assert status == 200
    assert body == {"ok": True, "entries": 3}


def test_write_ohne_ziel_gibt_500(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    monkeypatch.setattr(webapp.rekordbox, "write_rekordbox_xml", lambda _cfg: None)
    status, body = webapp.sync_rekordbox_xml(cfg)
    assert status == 500
    assert body["ok"] is False
    assert "Container-Log" in body["error"]


def test_fehlender_rekordbox_abschnitt_gibt_409(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.raw.pop("rekordbox")
    status, body = webapp.sync_rekordbox_xml(cfg)
    assert status == 409
    assert body == {
        "ok": False,
        "error": "Rekordbox-XML ist abgeschaltet (rekordbox.xml_enabled).",
    }


def test_rekordbox_kein_dict_gibt_409(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.raw["rekordbox"] = "kaputt"
    status, body = webapp.sync_rekordbox_xml(cfg)
    assert status == 409
    assert body["ok"] is False


def test_kaputte_ziel_xml_gibt_500_ohne_pfad(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    ziel = tmp_path / "out" / "sc-digger.xml"
    ziel.write_text("kein xml", encoding="utf-8")
    monkeypatch.setattr(webapp.rekordbox, "write_rekordbox_xml", lambda _cfg: ziel)

    client = client_for(cfg)
    resp = client.post(
        "/api/rekordbox/sync", auth=AUTH, headers={"content-type": "application/json"}
    )
    assert resp.status_code == 500
    assert resp.json()["ok"] is False
    assert str(tmp_path) not in resp.text


def test_route_erlaubt_leeren_aufruf_ohne_content_type(tmp_path):
    client = client_for(make_cfg(tmp_path))
    resp = client.post("/api/rekordbox/sync", auth=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "entries": 0}


def test_route_weist_falschen_content_type_ab(tmp_path):
    client = client_for(make_cfg(tmp_path))
    resp = client.post(
        "/api/rekordbox/sync",
        auth=AUTH,
        headers={"content-type": "text/plain"},
        content=b"x",
    )
    assert resp.status_code == 415


def test_statusseite_enthaelt_button_statuszeile_und_fetch(tmp_path):
    resp = client_for(make_cfg(tmp_path)).get("/", auth=AUTH)
    assert resp.status_code == 200
    assert "Rekordbox-XML neu schreiben" in resp.text
    assert 'id="rekordbox-sync"' in resp.text
    assert 'id="rekordbox-sync-status"' in resp.text
    assert "method: 'POST'" in resp.text
    assert "'Content-Type': 'application/json'" in resp.text
    assert "XML neu geschrieben: " in resp.text
