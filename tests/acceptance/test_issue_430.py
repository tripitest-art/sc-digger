"""Akzeptanztests: Rekordbox-Rückkanal – Dateinamen-Treffer sichtbar machen (#414, Teil 1)."""
import logging
import sqlite3
from pathlib import Path

from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.main import run_rekordbox_import
from sc_digger.models import Config
from sc_digger.rekordbox import RekordboxImportSummary, import_rekordbox_xml

PATH_MAP = {"/music/inbox": "Z:/Highres/_sc-digger-inbox"}
LAPTOP_A = "file://localhost/Z:/Highres/_sc-digger-inbox/150/1A/A.wav"
LAPTOP_B = "file://localhost/Z:/Highres/_sc-digger-inbox/150/1A/B.wav"
LAPTOP_C = "file://localhost/Z:/Highres/_sc-digger-inbox/150/1A/C.wav"


def _track(loc: str) -> str:
    return (
        f'<TRACK Name="T" Artist="A" Rating="204" PlayCount="3" '
        f'AverageBpm="150.00" Tonality="8A" Location="{loc}"/>'
    )


def _xml(*locations: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<DJ_PLAYLISTS Version="1.0.0">'
        f'<COLLECTION Entries="{len(locations)}">'
        + "".join(_track(loc) for loc in locations)
        + "</COLLECTION></DJ_PLAYLISTS>"
    )


def _cfg(tmp_path: Path, xml_file: Path) -> Config:
    return Config(raw={
        "rekordbox": {"path_map": PATH_MAP, "import_xml_path": str(xml_file)},
        "state": {
            "db_path": str(tmp_path / "seen.sqlite"),
            "track_db_path": str(tmp_path / "tracks.sqlite"),
        },
        "health": {"alert_after_bad_runs": 2},
    })


def _add(tmp_path: Path, server_path: str) -> None:
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.upsert_track(TrackRecord(
            path=server_path, mtime=1.0, size=10, status=TrackStatus.INBOX,
        ))


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


def test_summary_zaehlt_pfad_namens_und_fehltreffer_getrennt(tmp_path):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(_xml(LAPTOP_A, LAPTOP_B, LAPTOP_C), encoding="utf-8")
    _add(tmp_path, "/music/inbox/150/1A/A.wav")      # Pfad-Treffer
    _add(tmp_path, "/music/inbox/sortiert/B.wav")    # anderer Ordner: nur der Name passt
    # C.wav ist nirgends in der DB

    with TrackDB(tmp_path / "tracks.sqlite") as db:
        summary = import_rekordbox_xml(db, xml_file, PATH_MAP)

    assert isinstance(summary, RekordboxImportSummary)
    assert summary.total_tracks == 3
    assert summary.matched == 2
    assert summary.matched_by_path == 1
    assert summary.matched_by_name == 1
    assert summary.unmatched == 1
    assert summary.matched == summary.matched_by_path + summary.matched_by_name


def test_summary_felder_haben_default_null():
    summary = RekordboxImportSummary()
    assert summary.matched_by_path == 0
    assert summary.matched_by_name == 0


def test_nur_namenstreffer_ist_schlechter_lauf_mit_hinweis_auf_path_map(tmp_path):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(_xml(LAPTOP_B), encoding="utf-8")
    _add(tmp_path, "/music/inbox/sortiert/B.wav")    # nur über den Dateinamen findbar

    run_rekordbox_import(_cfg(tmp_path, xml_file), no_telegram=True)

    runs = _runs(tmp_path)
    assert len(runs) == 1
    ok, found, error = runs[0]
    assert ok == 0
    assert found == 1
    assert error and "path_map" in error and "Dateinamen" in error


def test_gemischter_lauf_mit_pfadtreffer_bleibt_ok(tmp_path):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(_xml(LAPTOP_A, LAPTOP_B), encoding="utf-8")
    _add(tmp_path, "/music/inbox/150/1A/A.wav")
    _add(tmp_path, "/music/inbox/sortiert/B.wav")

    run_rekordbox_import(_cfg(tmp_path, xml_file), no_telegram=True)

    assert _runs(tmp_path) == [(1, 2, None)]


def test_reiner_pfadtreffer_bleibt_ok(tmp_path):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(_xml(LAPTOP_A), encoding="utf-8")
    _add(tmp_path, "/music/inbox/150/1A/A.wav")

    run_rekordbox_import(_cfg(tmp_path, xml_file), no_telegram=True)

    assert _runs(tmp_path) == [(1, 1, None)]


def test_namens_only_laeufe_loesen_nach_schwelle_den_health_alarm_aus(tmp_path, caplog):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(_xml(LAPTOP_B), encoding="utf-8")
    _add(tmp_path, "/music/inbox/sortiert/B.wav")
    cfg = _cfg(tmp_path, xml_file)

    with caplog.at_level(logging.WARNING):
        run_rekordbox_import(cfg, no_telegram=True)
        assert "rekordbox_import" not in caplog.text  # erst ein schlechter Lauf: noch kein Alarm
        run_rekordbox_import(cfg, no_telegram=True)

    assert [r[0] for r in _runs(tmp_path)] == [0, 0]
    assert "rekordbox_import" in caplog.text
    assert "path_map" in caplog.text
