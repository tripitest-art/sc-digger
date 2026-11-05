"""Eigene Tests für den harmonischen Set-Builder im Web-Dashboard (Issue #340).

Deckt die Randfälle ab, die der Akzeptanztest offen lässt: das leere BPM-Fenster, nicht
verknüpfbare Tracks mit sichtbarer Schnitt-Segmentgrenze, die Auswahl per ``track_ids``,
Status- und Vollständigkeitsfilter der lesenden Abfrage sowie die M3U8-Ausgabe
(Dateiname, Segmentmarke und Escaping von Zeilenumbrüchen aus fremden Metadaten).
"""
from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.dj_export import Entry, order_harmonic
from sc_digger.models import Config
from sc_digger.web.app import _xml_filename, create_app, render_set_xml
from sc_digger.web.database import load_tracks_for_set_builder

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-340"
AUTH = ("sc", PASSWORD)


def make_cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def client_for(tmp_path: Path) -> TestClient:
    return TestClient(create_app(make_cfg(tmp_path), password=PASSWORD))


def seed_tracks(tmp_path: Path) -> dict[str, int]:
    """Legt Tracks an und liefert die IDs für die Auswahl per ``track_ids``.

    Sammlung: 8A/155, 8A/155,5, 9A/156, 2A/155 (nicht mit 8A verknüpfbar).
    Inbox: 8A/157. Ohne BPM bzw. ohne Key: dürfen nicht im Set landen.
    """
    ids: dict[str, int] = {}
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        def add(key: str, record: TrackRecord) -> None:
            ids[key] = int(db.upsert_track(record).id or 0)

        add("t1", TrackRecord(
            path="audio/t1.wav", mtime=1.0, size=10, status="archive",
            artist="A1", title="T1", bpm=155.0, key_camelot="8A",
        ))
        add("t2", TrackRecord(
            path="audio/t2.wav", mtime=1.0, size=10, status="archive",
            artist="A2", title="T2", bpm=155.5, key_camelot="8A",
        ))
        add("t3", TrackRecord(
            path="audio/t3.wav", mtime=1.0, size=10, status="archive",
            artist="A3", title="T3", bpm=156.0, key_camelot="9A",
        ))
        add("fern", TrackRecord(
            path="audio/fern.wav", mtime=1.0, size=10, status="archive",
            artist="A4", title="Fern", bpm=155.0, key_camelot="2A",
        ))
        add("inbox", TrackRecord(
            path="audio/inbox.wav", mtime=1.0, size=10, status="inbox",
            artist="A5", title="Inbox", bpm=157.0, key_camelot="8A",
        ))
        add("ohne_bpm", TrackRecord(
            path="audio/ohne_bpm.wav", mtime=1.0, size=10, status="archive",
            artist="A6", title="Ohne BPM", bpm=None, key_camelot="8A",
        ))
        add("ohne_key", TrackRecord(
            path="audio/ohne_key.wav", mtime=1.0, size=10, status="archive",
            artist="A7", title="Ohne Key", bpm=158.0, key_camelot=None,
        ))
        add("umbruch", TrackRecord(
            path="audio/umbruch.wav", mtime=1.0, size=10, status="archive",
            artist="A8\n#EXTM3U", title="Zeile\n#EXTINF:0,boese", bpm=155.0,
            key_camelot="8A",
        ))
    return ids


def test_loader_filtert_bpm_status_und_unvollstaendige_tracks(tmp_path: Path):
    seed_tracks(tmp_path)
    db_path = tmp_path / "tracks.sqlite"

    alle = load_tracks_for_set_builder(db_path)
    # Tracks ohne BPM oder ohne Key fehlen; sechs vollständige Tracks bleiben.
    assert len(alle) == 6
    assert all(entry.bpm is not None and entry.camelot for entry in alle)

    fenster = load_tracks_for_set_builder(db_path, bpm_min=150.0, bpm_max=156.0)
    assert sorted(entry.title for entry in fenster) == [
        "Fern", "T1", "T2", "T3", "Zeile\n#EXTINF:0,boese",
    ]

    inbox = load_tracks_for_set_builder(db_path, status="inbox")
    assert [entry.title for entry in inbox] == ["Inbox"]

    begrenzt = load_tracks_for_set_builder(db_path, limit=2)
    assert len(begrenzt) == 2


def test_loader_ohne_datenbank_ist_leer(tmp_path: Path):
    assert load_tracks_for_set_builder(tmp_path / "fehlt.sqlite") == []


def test_leeres_bpm_fenster_liefert_leere_playlist(tmp_path: Path):
    seed_tracks(tmp_path)
    client = client_for(tmp_path)

    antwort = client.post(
        "/api/set-builder", json={"bpm_min": 200.0, "bpm_max": 210.0}, auth=AUTH
    )
    assert antwort.status_code == 200
    data = antwort.json()
    assert data["ok"] is True
    assert data["total_tracks"] == 0
    assert data["segments"] == 0
    assert data["playlist"] == []
    assert data["transitions"] == []


def test_nicht_verknuepfbare_tracks_erzeugen_schnitt_segment(tmp_path: Path):
    seed_tracks(tmp_path)
    client = client_for(tmp_path)

    antwort = client.post(
        "/api/set-builder",
        json={"bpm_min": 150.0, "bpm_max": 156.0, "status": "archive"},
        auth=AUTH,
    )
    assert antwort.status_code == 200
    data = antwort.json()
    # 2A liegt sechs Schritte vom 8A-Block entfernt -> eigener Abschnitt.
    assert data["segments"] == 2
    schnitte = [t for t in data["transitions"] if t["typ"] == "schnitt"]
    assert len(schnitte) == 1
    assert schnitte[0]["kosten"] is None

    uebergaenge = [t for t in data["transitions"] if t["typ"] == "uebergang"]
    assert uebergaenge
    # Innerhalb eines Segments bleibt jeder Übergang unter der Kostengrenze.
    assert all(t["kosten"] <= 3.0 for t in uebergaenge)
    assert all(t["harmonisch"] in {"gleich", "rad_pm1", "relativ", "boost2"} for t in uebergaenge)


def test_track_ids_beschraenken_die_auswahl(tmp_path: Path):
    ids = seed_tracks(tmp_path)
    client = client_for(tmp_path)

    antwort = client.post(
        "/api/set-builder",
        json={"track_ids": [ids["t1"], ids["t3"]]},
        auth=AUTH,
    )
    assert antwort.status_code == 200
    data = antwort.json()
    assert data["total_tracks"] == 2
    assert {item["title"] for item in data["playlist"]} == {"T1", "T3"}


def test_status_filtert_die_auswahl(tmp_path: Path):
    seed_tracks(tmp_path)
    client = client_for(tmp_path)

    antwort = client.post(
        "/api/set-builder", json={"status": "inbox"}, auth=AUTH
    )
    assert antwort.status_code == 200
    assert [item["title"] for item in antwort.json()["playlist"]] == ["Inbox"]


def test_api_lehnt_kaputten_payload_ab(tmp_path: Path):
    seed_tracks(tmp_path)
    client = client_for(tmp_path)

    kaputt = client.post(
        "/api/set-builder", content="{", headers={"Content-Type": "application/json"},
        auth=AUTH,
    )
    assert kaputt.status_code == 400

    keine_liste = client.post("/api/set-builder", json={"track_ids": "1,2"}, auth=AUTH)
    assert keine_liste.status_code == 400

    keine_zahl = client.post("/api/set-builder", json={"track_ids": ["abc"]}, auth=AUTH)
    assert keine_zahl.status_code == 400


def test_m3u8_dateiname_und_header_werden_entschaerft(tmp_path: Path):
    seed_tracks(tmp_path)
    client = client_for(tmp_path)

    antwort = client.get(
        "/api/set-builder/m3u8",
        params={"bpm_min": 150, "bpm_max": 156, "name": 'Set\n#EXTM3U2"'},
    )
    # TestClient ergänzt die Basic-Anmeldung nicht automatisch, deshalb oben ohne auth
    # aufgerufen: dieselbe Anfrage mit Anmeldung liefert den Download.
    assert antwort.status_code == 401

    antwort = client.get(
        "/api/set-builder/m3u8",
        params={"bpm_min": 150, "bpm_max": 156, "name": 'Set\n#EXTM3U2"'},
        auth=AUTH,
    )
    assert antwort.status_code == 200
    disposition = antwort.headers["content-disposition"]
    assert disposition == 'attachment; filename="Set_EXTM3U2.m3u8"'
    assert "\n" not in disposition

    text = antwort.text
    # Genau eine #EXTM3U-Kopfzeile, auch wenn ein Titel eine solche Zeile enthält.
    assert [line for line in text.splitlines() if line == "#EXTM3U"] == ["#EXTM3U"]
    assert text.splitlines()[1] == '#PLAYLIST:Set #EXTM3U2"'
    assert "#EXTINF:0,boese" not in text.splitlines()
    # Der Umbruch im Titel landet eingeebnet in der EXTINF-Zeile.
    assert any("Zeile #EXTINF:0,boese" in line for line in text.splitlines())


def test_m3u8_enthaelt_segmentmarke_und_volle_pfade(tmp_path: Path):
    seed_tracks(tmp_path)
    client = client_for(tmp_path)

    antwort = client.get(
        "/api/set-builder/m3u8",
        params={"bpm_min": 150, "bpm_max": 156, "status": "archive"},
        auth=AUTH,
    )
    assert antwort.status_code == 200
    assert antwort.headers["content-type"].startswith("application/x-mpegurl")
    text = antwort.text
    assert text.startswith("#EXTM3U")
    assert "# Segment 2 (harmonisch fortlaufend)" in text
    assert "audio/t1.wav" in text
    assert "audio/fern.wav" in text


def test_m3u8_ohne_treffer_bleibt_gueltig(tmp_path: Path):
    seed_tracks(tmp_path)
    client = client_for(tmp_path)

    antwort = client.get(
        "/api/set-builder/m3u8", params={"bpm_min": 200, "bpm_max": 210}, auth=AUTH
    )
    assert antwort.status_code == 200
    assert antwort.text == "#EXTM3U\n#PLAYLIST:set\n"


def test_m3u8_entschaerft_pfad_mit_umbruch(tmp_path: Path):
    client = client_for(tmp_path)
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.upsert_track(TrackRecord(
            path="audio/boese.wav\n#EXTINF:0,x", mtime=1.0, size=10, status="archive",
            artist="A1", title="Pfad", bpm=155.0, key_camelot="8A",
        ))

    antwort = client.get("/api/set-builder/m3u8", auth=AUTH)
    assert antwort.status_code == 200
    zeilen = antwort.text.splitlines()
    # Auch der Pfad wird eingeebnet, statt eine eigene M3U8-Zeile einzuschleusen.
    assert "audio/boese.wav #EXTINF:0,x" in zeilen
    assert "#EXTINF:0,x" not in zeilen


def test_mix_seite_enthaelt_set_builder_formular(tmp_path: Path):
    client = client_for(tmp_path)

    antwort = client.get("/mix", auth=AUTH)
    assert antwort.status_code == 200
    assert 'id="set-builder-form"' in antwort.text
    assert 'id="sb-bpm-min"' in antwort.text
    assert 'id="sb-bpm-max"' in antwort.text
    assert "/api/set-builder/m3u8" in antwort.text


def test_mix_seite_trennt_auswahl_und_statusmeldung(tmp_path: Path):
    client = client_for(tmp_path)

    antwort = client.get("/mix", auth=AUTH)
    assert antwort.status_code == 200
    html = antwort.text
    # Auswahl und Meldung brauchen getrennte IDs: showSetStatus() schreibt per
    # textContent, sonst verschwinden die <option>s der Statusauswahl.
    assert html.count('id="sb-status"') == 1
    assert html.count('id="sb-message"') == 1
    assert "document.getElementById('sb-message')" in html
    assert 'for="sb-status"' in html


def test_xml_filename_sanitization():
    assert _xml_filename("mein set") == "mein_set.xml"
    assert _xml_filename('Set/\\#*?"') == "Set.xml"
    assert _xml_filename("") == "set.xml"
    assert _xml_filename("   ") == "set.xml"


def test_render_set_xml_empty_playlist():
    pl = order_harmonic([])
    xml_text = render_set_xml(pl, name="")
    assert xml_text.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    root = ET.fromstring(xml_text)
    assert root.tag == "DJ_PLAYLISTS"
    coll = root.find("COLLECTION")
    assert coll is not None and coll.attrib["Entries"] == "0"
    assert len(coll.findall("TRACK")) == 0
    node = root.find(".//PLAYLISTS//NODE[@Type='1']")
    assert node is not None
    assert node.attrib["Name"] == "set"
    assert node.attrib["Entries"] == "0"


def test_render_set_xml_with_path_map():
    entries = [
        Entry(
            path=Path("/music/inbox/155/track.wav"),
            artist="Artist",
            title="Title",
            bpm=155.0,
            camelot="8A",
            duration_s=180.2,
        )
    ]
    pl = order_harmonic(entries)
    path_map = {"/music/inbox": "D:/Rekordbox/Inbox"}
    xml_text = render_set_xml(pl, name="Mapped Set", path_map=path_map)
    root = ET.fromstring(xml_text)
    track = root.find("./COLLECTION/TRACK")
    assert track is not None
    assert "D:/Rekordbox/Inbox/155/track.wav" in track.attrib["Location"]
    assert track.attrib["AverageBpm"] == "155.00"
    assert track.attrib["TotalTime"] == "180"


def test_api_mix_export_xml_full_workflow(tmp_path: Path):
    seed_tracks(tmp_path)
    client = client_for(tmp_path)

    # 401 unauth
    unauth = client.post("/api/mix/export-xml", json={"bpm_min": 150, "bpm_max": 156})
    assert unauth.status_code == 401

    # 400 invalid json
    bad_json = client.post(
        "/api/mix/export-xml",
        data="kein-json",
        headers={"content-type": "application/json"},
        auth=AUTH,
    )
    assert bad_json.status_code == 400
    assert bad_json.json()["ok"] is False

    # 400 non-dict payload
    bad_payload = client.post("/api/mix/export-xml", json=["liste"], auth=AUTH)
    assert bad_payload.status_code == 400
    assert bad_payload.json()["ok"] is False

    # 400 non-list track_ids
    bad_type = client.post("/api/mix/export-xml", json={"track_ids": "1,2"}, auth=AUTH)
    assert bad_type.status_code == 400
    assert bad_type.json()["ok"] is False

    # 400 track_ids mit bool oder float
    bad_bool = client.post("/api/mix/export-xml", json={"track_ids": [True]}, auth=AUTH)
    assert bad_bool.status_code == 400
    assert bad_bool.json()["ok"] is False

    bad_float = client.post("/api/mix/export-xml", json={"track_ids": [1.5]}, auth=AUTH)
    assert bad_float.status_code == 400
    assert bad_float.json()["ok"] is False

    # 200 XML Attachment
    resp = client.post(
        "/api/mix/export-xml",
        json={"bpm_min": 150.0, "bpm_max": 156.0, "status": "archive", "name": "Archive Set"},
        auth=AUTH,
    )
    assert resp.status_code == 200
    assert "application/xml" in resp.headers["content-type"]
    assert 'filename="Archive_Set.xml"' in resp.headers["content-disposition"]

    root = ET.fromstring(resp.text)
    tracks = root.findall("./COLLECTION/TRACK")
    assert len(tracks) >= 2

    # XML Export mit leerer Filter-Auswahl
    empty_resp = client.post(
        "/api/mix/export-xml",
        json={"bpm_min": 200.0, "bpm_max": 210.0},
        auth=AUTH,
    )
    assert empty_resp.status_code == 200
    empty_root = ET.fromstring(empty_resp.text)
    assert empty_root.find("./COLLECTION").attrib["Entries"] == "0"
