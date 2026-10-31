"""Akzeptanztests: rekordbox.import_match_by_name schaltet den Namens-Fallback ab (#414, Teil 2)."""
import sqlite3
from pathlib import Path

import pytest

from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.main import run_rekordbox_import
from sc_digger.models import Config
from sc_digger.rekordbox import import_configured_xml, import_rekordbox_xml
from sc_digger.schema import get_field

ROOT = Path(__file__).resolve().parents[2]
PATH_MAP = {"/music/inbox": "Z:/Highres/_sc-digger-inbox"}
LAPTOP_A = "file://localhost/Z:/Highres/_sc-digger-inbox/150/1A/A.wav"
LAPTOP_B = "file://localhost/Z:/Highres/_sc-digger-inbox/150/1A/B.wav"
PFAD_A = "/music/inbox/150/1A/A.wav"
PFAD_B_ANDERER_ORDNER = "/music/inbox/sortiert/B.wav"   # nur über den Dateinamen findbar


def _xml(*locations: str) -> str:
    tracks = "".join(
        f'<TRACK Name="T" Artist="A" Rating="204" PlayCount="3" '
        f'AverageBpm="150.00" Tonality="8A" Location="{loc}"/>'
        for loc in locations
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><DJ_PLAYLISTS Version="1.0.0">'
        f'<COLLECTION Entries="{len(locations)}">{tracks}</COLLECTION></DJ_PLAYLISTS>'
    )


def _cfg(tmp_path: Path, xml_file: Path, **rb_extra) -> Config:
    return Config(raw={
        "rekordbox": {"path_map": PATH_MAP, "import_xml_path": str(xml_file), **rb_extra},
        "state": {
            "db_path": str(tmp_path / "seen.sqlite"),
            "track_db_path": str(tmp_path / "tracks.sqlite"),
        },
        "health": {"alert_after_bad_runs": 2},
    })


def _add(tmp_path: Path, server_path: str) -> int:
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        return db.upsert_track(TrackRecord(
            path=server_path, mtime=1.0, size=10, status=TrackStatus.INBOX,
        )).id


def _runs(tmp_path: Path) -> list[tuple]:
    conn = sqlite3.connect(str(tmp_path / "seen.sqlite"))
    try:
        return conn.execute(
            "SELECT ok, found, error FROM runs WHERE mode='rekordbox_import' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


# ------------------------------------------------------------------ import_rekordbox_xml

def test_standard_nutzt_den_namens_fallback(tmp_path):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(_xml(LAPTOP_B), encoding="utf-8")
    track_id = _add(tmp_path, PFAD_B_ANDERER_ORDNER)

    with TrackDB(tmp_path / "tracks.sqlite") as db:
        summary = import_rekordbox_xml(db, xml_file, PATH_MAP)
        assert summary.matched == 1
        assert db.get_track_by_id(track_id).rb_rating == 4


def test_ohne_namens_fallback_bleibt_der_namenstreffer_unmatched_und_ungeschrieben(tmp_path):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(_xml(LAPTOP_B), encoding="utf-8")
    track_id = _add(tmp_path, PFAD_B_ANDERER_ORDNER)

    with TrackDB(tmp_path / "tracks.sqlite") as db:
        summary = import_rekordbox_xml(db, xml_file, PATH_MAP, match_by_name=False)
        assert summary.total_tracks == 1
        assert summary.matched == 0
        assert summary.unmatched == 1
        rec = db.get_track_by_id(track_id)
        assert rec.rb_rating is None
        assert rec.rb_play_count is None
        assert rec.feedback is None


def test_ohne_namens_fallback_matcht_der_pfad_weiterhin(tmp_path):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(_xml(LAPTOP_A, LAPTOP_B), encoding="utf-8")
    a_id = _add(tmp_path, PFAD_A)
    b_id = _add(tmp_path, PFAD_B_ANDERER_ORDNER)

    with TrackDB(tmp_path / "tracks.sqlite") as db:
        summary = import_rekordbox_xml(db, xml_file, PATH_MAP, match_by_name=False)
        assert (summary.matched, summary.unmatched) == (1, 1)
        assert db.get_track_by_id(a_id).rb_rating == 4
        assert db.get_track_by_id(b_id).rb_rating is None


# ------------------------------------------------------------------ import_configured_xml

@pytest.mark.parametrize("extra, erwartet", [
    ({}, 1),                                      # Schlüssel fehlt: Standard True
    ({"import_match_by_name": True}, 1),
    ({"import_match_by_name": False}, 0),
    ({"import_match_by_name": "false"}, 1),       # kein echtes bool False: bleibt True
    ({"import_match_by_name": None}, 1),
])
def test_konfiguration_steuert_den_namens_fallback(tmp_path, extra, erwartet):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(_xml(LAPTOP_B), encoding="utf-8")
    _add(tmp_path, PFAD_B_ANDERER_ORDNER)
    cfg = _cfg(tmp_path, xml_file, **extra)

    with TrackDB(tmp_path / "tracks.sqlite") as db:
        summary = import_configured_xml(db, cfg)
    assert summary.matched == erwartet


# ------------------------------------------------------------------ Health-Wirkung

def test_falsche_path_map_ohne_namens_fallback_ist_schlechter_lauf(tmp_path):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(_xml(LAPTOP_B), encoding="utf-8")
    _add(tmp_path, PFAD_B_ANDERER_ORDNER)

    run_rekordbox_import(_cfg(tmp_path, xml_file, import_match_by_name=False), no_telegram=True)

    assert _runs(tmp_path) == [(1, 0, None)]   # found == 0 zaehlt in Health.evaluate als schlecht


# ------------------------------------------------------------------ Schema und Konfiguration

def test_schema_kennt_die_option_als_editierbares_bool():
    feld = get_field("rekordbox.import_match_by_name")
    assert feld.kind == "bool"
    assert feld.editable is True
    assert feld.label and feld.help


def test_config_yaml_setzt_den_standard_auf_true():
    zeilen = [z.strip() for z in (ROOT / "config.yaml").read_text(encoding="utf-8").splitlines()]
    assert "import_match_by_name: true" in zeilen
