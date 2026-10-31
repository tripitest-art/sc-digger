"""Unit-Tests für die Option rekordbox.import_match_by_name (#431)."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from sc_digger.rekordbox import import_configured_xml, import_rekordbox_xml

PATH_MAP = {"/music/inbox": "Z:/Highres/_sc-digger-inbox"}
LAPTOP_A = "file://localhost/Z:/Highres/_sc-digger-inbox/150/1A/track.wav"
LAPTOP_B = "file://localhost/Z:/Highres/_sc-digger-inbox/150/1A/other.wav"
PFAD_EXAKT = "/music/inbox/150/1A/track.wav"
PFAD_ANDERER_ORDNER = "/music/inbox/sortiert/other.wav"


def _xml(*locations: str) -> str:
    tracks = "".join(
        f'<TRACK Name="T" Artist="A" Rating="204" PlayCount="2" '
        f'AverageBpm="150.00" Tonality="8A" Location="{loc}"/>'
        for loc in locations
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><DJ_PLAYLISTS Version="1.0.0">'
        f'<COLLECTION Entries="{len(locations)}">{tracks}</COLLECTION></DJ_PLAYLISTS>'
    )


def test_import_rekordbox_xml_match_by_name_false_ruft_get_track_by_filename_nicht_auf(tmp_path):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(_xml(LAPTOP_B), encoding="utf-8")

    db = MagicMock(spec=TrackDB)
    db.get_track_by_path.return_value = None

    summary = import_rekordbox_xml(db, xml_file, PATH_MAP, match_by_name=False)

    assert summary.total_tracks == 1
    assert summary.matched == 0
    assert summary.unmatched == 1
    db.get_track_by_path.assert_called_once_with("/music/inbox/150/1A/other.wav")
    db.get_track_by_filename.assert_not_called()
    db.update_rekordbox_metadata.assert_not_called()


def test_import_rekordbox_xml_match_by_name_true_ruft_get_track_by_filename_auf(tmp_path):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(_xml(LAPTOP_B), encoding="utf-8")

    db = MagicMock(spec=TrackDB)
    db.get_track_by_path.return_value = None
    rec = TrackRecord(id=42, path=PFAD_ANDERER_ORDNER, mtime=1.0, size=10, status=TrackStatus.INBOX)
    db.get_track_by_filename.return_value = rec

    summary = import_rekordbox_xml(db, xml_file, PATH_MAP, match_by_name=True)

    assert summary.total_tracks == 1
    assert summary.matched == 1
    assert summary.unmatched == 0
    db.get_track_by_filename.assert_called_once_with("other.wav")
    db.update_rekordbox_metadata.assert_called_once()


@pytest.mark.parametrize("value, expected_match_by_name", [
    (False, False),
    (True, True),
    (None, True),
    ("false", True),
    (0, True),
    (1, True),
    ([], True),
    ({}, True),
])
def test_import_configured_xml_nur_echtes_bool_false_schaltet_ab(tmp_path, value, expected_match_by_name, monkeypatch):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(_xml(LAPTOP_A), encoding="utf-8")

    cfg = Config(raw={
        "rekordbox": {
            "path_map": PATH_MAP,
            "import_xml_path": str(xml_file),
            "import_match_by_name": value,
        }
    })

    recorded = []
    import sc_digger.rekordbox as rb_mod
    orig_fn = rb_mod.import_rekordbox_xml

    def fake_import(db, p, map_, *, auto_like=True, match_by_name=True):
        recorded.append(match_by_name)
        return orig_fn(db, p, map_, auto_like=auto_like, match_by_name=match_by_name)

    monkeypatch.setattr(rb_mod, "import_rekordbox_xml", fake_import)

    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.upsert_track(TrackRecord(path=PFAD_EXAKT, mtime=1.0, size=10, status=TrackStatus.INBOX))
        import_configured_xml(db, cfg)

    assert recorded == [expected_match_by_name]


def test_import_rekordbox_xml_exakter_pfad_matcht_auch_bei_match_by_name_false(tmp_path):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(_xml(LAPTOP_A), encoding="utf-8")

    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        t_id = db.upsert_track(TrackRecord(path=PFAD_EXAKT, mtime=1.0, size=10, status=TrackStatus.INBOX)).id
        summary = import_rekordbox_xml(db, xml_file, PATH_MAP, match_by_name=False)

        assert summary.total_tracks == 1
        assert summary.matched == 1
        assert summary.matched_by_path == 1
        assert summary.matched_by_name == 0
        assert summary.unmatched == 0
        rec = db.get_track_by_id(t_id)
        assert rec.rb_rating == 4
        assert rec.rb_play_count == 2
