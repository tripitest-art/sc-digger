"""Akzeptanztests: Rekordbox-XML per Dashboard neu schreiben (POST /api/rekordbox/sync)."""
import xml.etree.ElementTree as ET
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web import app as webapp
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-xml-sync"
AUTH = ("x", PASSWORD)
JSON = {"content-type": "application/json"}
ORIGIN = {"origin": "http://testserver"}


def make_cfg(tmp_path, *, enabled=True, xml_in_collection=False):
    cfg = Config.load(ROOT / "config.yaml")
    inbox = tmp_path / "inbox"
    collection = tmp_path / "sammlung"
    inbox.mkdir()
    collection.mkdir()
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    cfg.raw["download"] = {
        **cfg.raw.get("download", {}),
        "inbox_dir": str(inbox),
        "collection_dir": str(collection),
    }
    xml_dir = collection if xml_in_collection else tmp_path / "out"
    xml_dir.mkdir(exist_ok=True)
    cfg.raw["rekordbox"] = {
        "xml_enabled": enabled,
        "xml_path": str(xml_dir / "sc-digger.xml"),
        "path_map": {},
    }
    return cfg


def client_for(cfg):
    return TestClient(create_app(cfg, password=PASSWORD))


def test_sync_schreibt_xml_und_meldet_anzahl(tmp_path):
    cfg = make_cfg(tmp_path)
    resp = client_for(cfg).post("/api/rekordbox/sync", auth=AUTH, headers={**JSON, **ORIGIN})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "entries": 0}
    xml = tmp_path / "out" / "sc-digger.xml"
    assert xml.is_file()
    assert ET.parse(xml).getroot().find("COLLECTION").get("Entries") == "0"


def test_antwort_nennt_keinen_dateipfad(tmp_path):
    cfg = make_cfg(tmp_path)
    resp = client_for(cfg).post("/api/rekordbox/sync", auth=AUTH, headers=JSON)
    assert str(tmp_path) not in resp.text


def test_abgeschaltet_gibt_409_und_schreibt_nichts(tmp_path):
    cfg = make_cfg(tmp_path, enabled=False)
    resp = client_for(cfg).post("/api/rekordbox/sync", auth=AUTH, headers=JSON)
    assert resp.status_code == 409
    assert resp.json() == {
        "ok": False,
        "error": "Rekordbox-XML ist abgeschaltet (rekordbox.xml_enabled).",
    }
    assert not (tmp_path / "out" / "sc-digger.xml").exists()


def test_ziel_in_der_sammlung_gibt_500_und_schreibt_nichts(tmp_path):
    cfg = make_cfg(tmp_path, xml_in_collection=True)
    resp = client_for(cfg).post("/api/rekordbox/sync", auth=AUTH, headers=JSON)
    assert resp.status_code == 500
    assert resp.json() == {
        "ok": False,
        "error": "Rekordbox-XML konnte nicht geschrieben werden, siehe Container-Log.",
    }
    assert not (tmp_path / "sammlung" / "sc-digger.xml").exists()


def test_ausnahme_beim_schreiben_gibt_500(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)

    def kaputt(_cfg):
        raise RuntimeError("Platte voll")

    monkeypatch.setattr(webapp.rekordbox, "write_rekordbox_xml", kaputt)
    status, body = webapp.sync_rekordbox_xml(cfg)
    assert status == 500
    assert body["ok"] is False
    assert "Platte voll" not in body["error"]


def test_ohne_anmeldung_401(tmp_path):
    resp = client_for(make_cfg(tmp_path)).post("/api/rekordbox/sync", headers=JSON)
    assert resp.status_code == 401


def test_fremde_origin_403_und_falscher_content_type_415(tmp_path):
    client = client_for(make_cfg(tmp_path))
    fremd = client.post(
        "/api/rekordbox/sync", auth=AUTH, headers={**JSON, "origin": "http://boese.example"}
    )
    assert fremd.status_code == 403
    text = client.post(
        "/api/rekordbox/sync", auth=AUTH, headers={"content-type": "text/plain"}, content=b"x"
    )
    assert text.status_code == 415
    assert not (tmp_path / "out" / "sc-digger.xml").exists()


def test_statusseite_hat_button_und_skript(tmp_path):
    resp = client_for(make_cfg(tmp_path)).get("/", auth=AUTH)
    assert resp.status_code == 200
    assert 'id="rekordbox-sync"' in resp.text
    assert 'id="rekordbox-sync-status"' in resp.text
    assert "/api/rekordbox/sync" in resp.text
