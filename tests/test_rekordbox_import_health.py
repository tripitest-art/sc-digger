"""Dedizierte Unit-Tests für Rekordbox-Import Health und Zähler (Issue #430)."""
import logging
import sqlite3
from pathlib import Path

from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.main import REKORDBOX_NUR_NAMENSTREFFER, run_rekordbox_import
from sc_digger.models import Config
from sc_digger.rekordbox import (
    RekordboxImportSummary,
    import_rekordbox_xml,
)

PATH_MAP = {"/music/inbox": "Z:/Highres/_sc-digger-inbox"}
LAPTOP_A = "file://localhost/Z:/Highres/_sc-digger-inbox/150/1A/A.wav"
LAPTOP_B = "file://localhost/Z:/Highres/_sc-digger-inbox/150/1A/B.wav"


def _xml(*locations: str) -> str:
    tracks = "".join(
        f'<TRACK Name="T" Artist="A" Rating="204" PlayCount="3" Location="{loc}"/>'
        for loc in locations
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<DJ_PLAYLISTS Version="1.0.0">\n'
        f'  <COLLECTION Entries="{len(locations)}">\n'
        f"    {tracks}\n"
        "  </COLLECTION>\n"
        "</DJ_PLAYLISTS>\n"
    )


def _add(tmp_path: Path, server_path: str) -> None:
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.upsert_track(TrackRecord(
            path=server_path, mtime=1.0, size=10, status=TrackStatus.INBOX,
        ))


def _cfg(tmp_path: Path, xml_path: Path) -> Config:
    return Config(raw={
        "state": {
            "db_path": str(tmp_path / "state.sqlite"),
            "track_db_path": str(tmp_path / "tracks.sqlite"),
        },
        "rekordbox": {
            "import_xml_path": str(xml_path),
            "path_map": dict(PATH_MAP),
        },
        "health": {"alert_after_bad_runs": 2},
    })


def _runs(tmp_path: Path) -> list[tuple]:
    conn = sqlite3.connect(tmp_path / "state.sqlite")
    try:
        return conn.execute(
            "SELECT ok, found, error FROM runs WHERE mode='rekordbox_import' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def test_summary_zaehler_bei_leerer_xml(tmp_path: Path):
    xml_file = tmp_path / "empty.xml"
    xml_file.write_text(_xml(), encoding="utf-8")

    with TrackDB(tmp_path / "tracks.sqlite") as db:
        summary = import_rekordbox_xml(db, xml_file, PATH_MAP)

    assert isinstance(summary, RekordboxImportSummary)
    assert summary.total_tracks == 0
    assert summary.matched == 0
    assert summary.matched_by_path == 0
    assert summary.matched_by_name == 0
    assert summary.unmatched == 0


def test_summary_invariante_bei_gemischtem_matching(tmp_path: Path):
    xml_file = tmp_path / "mixed.xml"
    xml_file.write_text(_xml(LAPTOP_A, LAPTOP_B), encoding="utf-8")
    _add(tmp_path, "/music/inbox/150/1A/A.wav")      # Pfad-Treffer
    _add(tmp_path, "/music/inbox/sortiert/B.wav")    # Namen-Treffer

    with TrackDB(tmp_path / "tracks.sqlite") as db:
        summary = import_rekordbox_xml(db, xml_file, PATH_MAP)

    assert summary.total_tracks == 2
    assert summary.matched == 2
    assert summary.matched_by_path == 1
    assert summary.matched_by_name == 1
    assert summary.matched == summary.matched_by_path + summary.matched_by_name


def test_entwarnung_nach_pfadtreffer_lauf(tmp_path: Path, caplog):
    xml_b = tmp_path / "export_b.xml"
    xml_b.write_text(_xml(LAPTOP_B), encoding="utf-8")
    xml_a = tmp_path / "export_a.xml"
    xml_a.write_text(_xml(LAPTOP_A), encoding="utf-8")

    _add(tmp_path, "/music/inbox/150/1A/A.wav")      # Matcht LAPTOP_A per Pfad
    _add(tmp_path, "/music/inbox/sortiert/B.wav")    # Matcht LAPTOP_B nur per Name

    cfg_b = _cfg(tmp_path, xml_b)
    cfg_a = _cfg(tmp_path, xml_a)

    # 2 schlechte Läufe (nur Name) -> Alarm
    with caplog.at_level(logging.WARNING):
        run_rekordbox_import(cfg_b, no_telegram=True)
        run_rekordbox_import(cfg_b, no_telegram=True)
        assert "rekordbox_import" in caplog.text

    runs = _runs(tmp_path)
    assert len(runs) == 2
    assert runs[0][0] == 0 and runs[1][0] == 0
    assert runs[0][2].startswith(REKORDBOX_NUR_NAMENSTREFFER)

    # 3. Lauf mit Pfad-Treffer -> ok=True, Entwarnung
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        run_rekordbox_import(cfg_a, no_telegram=True)

    runs = _runs(tmp_path)
    assert len(runs) == 3
    assert runs[2] == (1, 1, None)


def test_leere_xml_bleibt_ok_und_found_null(tmp_path: Path):
    xml_file = tmp_path / "empty.xml"
    xml_file.write_text(_xml(), encoding="utf-8")

    run_rekordbox_import(_cfg(tmp_path, xml_file), no_telegram=True)

    assert _runs(tmp_path) == [(1, 0, None)]
