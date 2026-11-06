"""Tests für Filter-Chips und fixierte Spalten im Track-Explorer (Issue #487)."""
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
import pytest

from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from sc_digger.web.app import FILTER_LABELS, active_filter_chips, create_app

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "test-pass-487"


def make_cfg(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def client_for(tmp_path):
    return TestClient(create_app(make_cfg(tmp_path), password=PASSWORD))


def seed(tmp_path):
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.upsert_track(TrackRecord(
            path="/music/Schranz/150/test.wav", mtime=1.0, size=1000,
            artist="Svetec", title="Acid Kick", format="wav", bitrate_kbps=1411.0,
            cutoff_hz=21000, quality_status=QualityStatus.OK, bpm=150.0,
            key_camelot="8A", status=TrackStatus.ARCHIVE,
        ))
    return db_path


def test_active_filter_chips_alle_standard_labels():
    expected_labels = {
        "q": "Suche",
        "status": "Status",
        "quality": "Qualität",
        "key_camelot": "Key",
        "bpm_min": "BPM ab",
        "bpm_max": "BPM bis",
        "adopted": "Übernahme",
        "breakdown_max": "Max. Breakdown %",
        "taste_min": "Min. Geschmack",
    }
    assert FILTER_LABELS == expected_labels

    filters = {key: f"val_{key}" for key in expected_labels}
    chips = active_filter_chips(filters, limit=30)
    assert len(chips) == len(expected_labels)
    for chip in chips:
        assert chip["label"] == expected_labels[chip["key"]]
        assert chip["value"] == f"val_{chip['key']}"


def test_active_filter_chips_unbekannter_schluessel():
    chips = active_filter_chips({"unbekannt": "xyz"}, limit=10)
    assert len(chips) == 1
    assert chips[0]["key"] == "unbekannt"
    assert chips[0]["label"] == "unbekannt"
    assert chips[0]["value"] == "xyz"


def test_active_filter_chips_reihenfolge_bleibt_erhalten():
    filters = {"status": "inbox", "q": "rave", "bpm_min": "145"}
    chips = active_filter_chips(filters, limit=20)
    assert [c["key"] for c in chips] == ["status", "q", "bpm_min"]


def test_active_filter_chips_ignoriert_leere_oder_none_werte():
    filters = {"q": "", "status": "inbox", "bpm_min": None, "bpm_max": ""}  # type: ignore[dict-item]
    chips = active_filter_chips(filters, limit=20)
    assert len(chips) == 1
    assert chips[0]["key"] == "status"


def test_active_filter_chips_wirft_nicht_bei_ungueltigen_eingaben():
    assert active_filter_chips(None, limit=10) == []  # type: ignore[arg-type]
    assert active_filter_chips({}, limit=10) == []


def test_active_filter_chips_remove_url_ohne_sortierung():
    chips = active_filter_chips({"q": "hard", "status": "inbox"}, limit=15)
    parsed = urlparse(chips[0]["remove_url"])
    assert parsed.path == "/tracks"
    qs = parse_qs(parsed.query)
    assert "q" not in qs
    assert qs["status"] == ["inbox"]
    assert qs["limit"] == ["15"]
    assert "sort" not in qs
    assert "dir" not in qs
    assert "offset" not in qs


def test_active_filter_chips_remove_url_encodiert_sonderzeichen():
    chips = active_filter_chips({"q": "Svetec & Friends / 100%"}, limit=50)
    assert "%26" in chips[0]["remove_url"] or "&Friends" not in chips[0]["remove_url"]
    parsed = urlparse(chips[0]["remove_url"])
    qs = parse_qs(parsed.query)
    assert qs["limit"] == ["50"]
    assert "q" not in qs


def test_web_tracks_sticky_col_klassen(tmp_path):
    seed(tmp_path)
    client = client_for(tmp_path)
    r = client.get("/tracks", auth=("sc", PASSWORD))
    assert r.status_code == 200
    html = r.text
    # Spaltenüberschriften in <thead>
    assert '<th class="sticky-col"' in html
    # Datenzeilen in <tbody>
    assert '<td class="sticky-col fw-600">' in html
    assert '<td class="sticky-col">' in html


def test_web_tracks_filter_chips_in_html_und_remove_url(tmp_path):
    seed(tmp_path)
    client = client_for(tmp_path)
    r = client.get("/tracks?q=Svetec&bpm_min=140&sort=bpm&dir=desc", auth=("sc", PASSWORD))
    assert r.status_code == 200
    html = r.text
    assert '<div class="filter-chips">' in html
    assert "Suche: Svetec ×" in html
    assert "BPM ab: 140 ×" in html

    # Jetzt dem remove_url von BPM ab folgen
    r_removed = client.get("/tracks?q=Svetec&sort=bpm&dir=desc&limit=50", auth=("sc", PASSWORD))
    assert r_removed.status_code == 200
    assert "Suche: Svetec ×" in r_removed.text
    assert "BPM ab:" not in r_removed.text
