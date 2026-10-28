"""Akzeptanztests: Rekordbox-Rückkanal im täglichen Lauf anschließen (rekordbox.import_xml_path)."""
import logging
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.main import run_rekordbox_import
from sc_digger.models import Config
from sc_digger.rekordbox import RekordboxImportSummary, import_configured_xml

PATH_MAP = {"/music/inbox": "Z:/Highres/_sc-digger-inbox"}
SERVER_PATH = "/music/inbox/150/1A/A.wav"
LAPTOP_LOCATION = "file://localhost/Z:/Highres/_sc-digger-inbox/150/1A/A.wav"


def _xml(location: str = LAPTOP_LOCATION, rating: str = "204") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<DJ_PLAYLISTS Version="1.0.0">'
        '<COLLECTION Entries="1">'
        f'<TRACK Name="T" Artist="A" Rating="{rating}" PlayCount="3" '
        f'AverageBpm="150.00" Tonality="8A" Location="{location}">'
        '<POSITION_MARK Name="Kick" Type="0" Start="1.5" Num="0"/>'
        "</TRACK></COLLECTION></DJ_PLAYLISTS>"
    )


def _cfg(tmp_path: Path, import_xml_path) -> Config:
    rb: dict = {"path_map": PATH_MAP}
    if import_xml_path is not None:
        rb["import_xml_path"] = import_xml_path
    return Config(raw={
        "rekordbox": rb,
        "state": {
            "db_path": str(tmp_path / "seen.sqlite"),
            "track_db_path": str(tmp_path / "tracks.sqlite"),
        },
        "health": {"alert_after_bad_runs": 2},
    })


def _add_track(tmp_path: Path) -> int:
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        rec = db.upsert_track(TrackRecord(
            path=SERVER_PATH, mtime=1.0, size=10, status=TrackStatus.INBOX,
        ))
        return rec.id


def _runs(tmp_path: Path) -> list[tuple]:
    path = tmp_path / "seen.sqlite"
    if not path.exists():
        return []
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(
            "SELECT ok, found, error FROM runs WHERE mode='rekordbox_import' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


# ------------------------------------------------------------------ import_configured_xml

@pytest.mark.parametrize("value", [None, "", "   ", 0])
def test_leerer_schluessel_ist_feature_aus(tmp_path, caplog, value):
    cfg = _cfg(tmp_path, value)
    with caplog.at_level(logging.WARNING):
        with TrackDB(tmp_path / "tracks.sqlite") as db:
            assert import_configured_xml(db, cfg) is None
    assert caplog.records == []


def test_fehlender_abschnitt_ist_kein_fehler(tmp_path):
    cfg = Config(raw={})
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        assert import_configured_xml(db, cfg) is None


def test_fehlende_datei_wird_geloggt_nicht_geworfen(tmp_path, caplog):
    fehlend = tmp_path / "rueckkanal" / "export.xml"
    cfg = _cfg(tmp_path, str(fehlend))
    with caplog.at_level(logging.WARNING):
        with TrackDB(tmp_path / "tracks.sqlite") as db:
            assert import_configured_xml(db, cfg) is None
    assert str(fehlend) in caplog.text


def test_import_nutzt_path_map_und_schreibt_metadaten(tmp_path):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(_xml(), encoding="utf-8")
    track_id = _add_track(tmp_path)
    cfg = _cfg(tmp_path, str(xml_file))

    with TrackDB(tmp_path / "tracks.sqlite") as db:
        summary = import_configured_xml(db, cfg)
        assert isinstance(summary, RekordboxImportSummary)
        assert summary.total_tracks == 1
        assert summary.matched == 1
        assert summary.unmatched == 0
        assert summary.ratings_count == 1
        assert summary.played_count == 1
        assert summary.auto_likes_count == 1

        rec = db.get_track_by_id(track_id)
        assert rec.rb_rating == 4
        assert rec.rb_play_count == 3
        assert rec.rb_bpm == pytest.approx(150.0)
        assert rec.rb_key == "8A"
        assert rec.feedback == "like"
        assert "1.5" in rec.rb_cues


def test_import_ist_idempotent(tmp_path):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(_xml(), encoding="utf-8")
    _add_track(tmp_path)
    cfg = _cfg(tmp_path, str(xml_file))

    with TrackDB(tmp_path / "tracks.sqlite") as db:
        erste = import_configured_xml(db, cfg)
        zweite = import_configured_xml(db, cfg)
    assert erste.matched == zweite.matched == 1
    assert zweite.auto_likes_count == 0  # Feedback war schon gesetzt


def test_kaputte_xml_propagiert_den_fehler(tmp_path):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text("<DJ_PLAYLISTS><COLLECTION>", encoding="utf-8")
    cfg = _cfg(tmp_path, str(xml_file))
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        with pytest.raises(ET.ParseError):
            import_configured_xml(db, cfg)


# ------------------------------------------------------------------ run_rekordbox_import

def test_run_ohne_schluessel_schreibt_nichts(tmp_path):
    run_rekordbox_import(_cfg(tmp_path, ""), no_telegram=True)
    assert _runs(tmp_path) == []


def test_run_mit_fehlender_datei_wirft_nicht_und_schreibt_keinen_lauf(tmp_path):
    run_rekordbox_import(_cfg(tmp_path, str(tmp_path / "gibt-es-nicht.xml")), no_telegram=True)
    assert _runs(tmp_path) == []


def test_run_erfolg_wird_im_health_protokoll_vermerkt(tmp_path):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(_xml(), encoding="utf-8")
    track_id = _add_track(tmp_path)

    run_rekordbox_import(_cfg(tmp_path, str(xml_file)), no_telegram=True)

    assert _runs(tmp_path) == [(1, 1, None)]
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        assert db.get_track_by_id(track_id).rb_rating == 4


def test_run_mit_kaputter_xml_bricht_nicht_ab_und_ist_im_health_sichtbar(tmp_path):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text("das ist kein xml", encoding="utf-8")

    run_rekordbox_import(_cfg(tmp_path, str(xml_file)), no_telegram=True)  # wirft nicht

    runs = _runs(tmp_path)
    assert len(runs) == 1
    ok, found, error = runs[0]
    assert ok == 0
    assert found == 0
    assert error and "ParseError" in error


def test_run_ohne_treffer_zaehlt_als_schlechter_lauf(tmp_path):
    """Falsche path_map: XML ist gültig, aber kein Track wird gefunden."""
    xml_file = tmp_path / "export.xml"
    # Dateiname bewusst anders als SERVER_PATH (.../A.wav): import_rekordbox_xml
    # faellt nach dem Pfad auf den Dateinamen zurueck, und der soll hier nicht greifen.
    xml_file.write_text(_xml(location="file://localhost/Q:/anderswo/B.wav"), encoding="utf-8")
    _add_track(tmp_path)

    run_rekordbox_import(_cfg(tmp_path, str(xml_file)), no_telegram=True)

    assert _runs(tmp_path) == [(1, 0, None)]
