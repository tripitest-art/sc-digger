"""Akzeptanztests: Rekordbox-XML-Export im Set-Builder (/mix & POST /api/mix/export-xml)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.dj_export import TrackEntry, order_harmonic
from sc_digger.models import Config
from sc_digger.web.app import create_app, render_set_xml
from tests.test_web_tracks_ui import TestClient

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "test-xml-export-pw"
AUTH = ("sc", PASSWORD)


def _make_cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def test_render_set_xml_contains_collection_and_ordered_playlist():
    entries = [
        TrackEntry(
            path=Path("inbox/t1.wav"),
            artist="Svetec",
            title="Acid Tool",
            bpm=155.0,
            camelot="8A",
            duration_s=310.0,
        ),
        TrackEntry(
            path=Path("inbox/t2.flac"),
            artist="Viper XXL",
            title="Hard Groove",
            bpm=156.0,
            camelot="9A",
            duration_s=295.4,
        ),
    ]
    playlist = order_harmonic(entries)
    xml_text = render_set_xml(playlist, name="Peaktime Set")

    assert xml_text.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    root = ET.fromstring(xml_text)
    assert root.tag == "DJ_PLAYLISTS"

    collection = root.find("COLLECTION")
    assert collection is not None
    assert collection.attrib.get("Entries") == "2"
    tracks = collection.findall("TRACK")
    assert len(tracks) == 2
    assert tracks[0].attrib["Name"] == "Acid Tool"
    assert tracks[0].attrib["Artist"] == "Svetec"
    assert tracks[0].attrib["Tonality"] == "8A"
    assert tracks[0].attrib["AverageBpm"] == "155.00"
    assert tracks[0].attrib["TotalTime"] == "310"
    assert "file://localhost/" in tracks[0].attrib["Location"]

    playlist_node = root.find(".//PLAYLISTS//NODE[@Type='1']")
    assert playlist_node is not None
    assert playlist_node.attrib.get("Name") == "Peaktime Set"
    keys = [n.attrib["Key"] for n in playlist_node.findall("TRACK")]
    assert keys == ["1", "2"]


def test_post_api_mix_export_xml_returns_xml_attachment(tmp_path: Path):
    cfg = _make_cfg(tmp_path)
    db_path = cfg["state"]["track_db_path"]

    with TrackDB(db_path) as db:
        db.upsert_track(TrackRecord(
            path="inbox/155/a.wav", mtime=1.0, size=100, status="inbox",
            artist="A1", title="T1", bpm=155.0, key_camelot="8A", duration=300.0,
        ))
        db.upsert_track(TrackRecord(
            path="inbox/156/b.wav", mtime=2.0, size=100, status="inbox",
            artist="A2", title="T2", bpm=156.0, key_camelot="8A", duration=320.0,
        ))

    client = TestClient(create_app(cfg, password=PASSWORD))

    # Ohne Auth -> 401
    unauth = client.post("/api/mix/export-xml", json={"bpm_min": 150, "bpm_max": 160})
    assert unauth.status_code == 401

    # Gültiger Export -> 200 XML Attachment
    resp = client.post(
        "/api/mix/export-xml",
        json={"bpm_min": 150.0, "bpm_max": 160.0, "status": "inbox", "name": "mein_set"},
        auth=AUTH,
    )
    assert resp.status_code == 200
    assert "xml" in resp.headers.get("content-type", "").lower()
    assert 'filename="mein_set.xml"' in resp.headers.get("content-disposition", "")

    root = ET.fromstring(resp.text)
    tracks = root.findall("./COLLECTION/TRACK")
    assert len(tracks) == 2


def test_post_api_mix_export_xml_rejects_invalid_payload(tmp_path: Path):
    cfg = _make_cfg(tmp_path)
    client = TestClient(create_app(cfg, password=PASSWORD))

    bad_ids = client.post(
        "/api/mix/export-xml",
        json={"track_ids": ["kein-int"]},
        auth=AUTH,
    )
    assert bad_ids.status_code == 400
    assert bad_ids.json()["ok"] is False


def test_mix_page_contains_rekordbox_xml_export_button(tmp_path: Path):
    cfg = _make_cfg(tmp_path)
    client = TestClient(create_app(cfg, password=PASSWORD))
    resp = client.get("/mix", auth=AUTH)
    assert resp.status_code == 200
    assert 'id="sb-export-xml"' in resp.text
    assert "/api/mix/export-xml" in resp.text
