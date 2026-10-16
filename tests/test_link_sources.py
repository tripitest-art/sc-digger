"""Eigene Tests für scripts/link_sources.py und SoundCloudClient.search_tracks."""
from pathlib import Path
from unittest.mock import patch

import pytest

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.soundcloud import SoundCloudError
from scripts.link_sources import LinkStats, _search_query, main, run_link
from tests.test_modes import FakeSC, mk, raw_track


def cand(i, title, artist="uploader"):
    return mk(
        i,
        title=title,
        permalink_url=f"https://soundcloud.com/{artist}/t{i}",
        user={"username": artist, "permalink_url": f"https://soundcloud.com/{artist}"},
    )


class FakeSCForMain(FakeSC):
    def __init__(self):
        super().__init__({})

    def likes_of_user(self, url, limit=2000):
        return "Likes", [cand(10, "Artist A - Title A")]

    def playlist_tracks(self, url, limit=None):
        return "Liste", []

    def search_tracks(self, query, limit=5):
        return []


def test_search_tracks_limit():
    """FakeSC-Test für search_tracks mit limit: mehr Treffer in Route, als abgefragt werden."""
    tracks_data = [raw_track(i) for i in range(1, 11)]

    def route(params):
        return {"collection": tracks_data}

    sc = FakeSC({"/search/tracks": route})
    res = sc.search_tracks("test query", limit=3)
    assert len(res) == 3
    assert [t.id for t in res] == [1, 2, 3]


def test_search_query_generation():
    # Beide gesetzt
    r1 = TrackRecord(path="/sammlung/ignored.wav", mtime=1.0, size=1, artist="Svetec", title="Acid Kick")
    assert _search_query(r1) == "Svetec - Acid Kick"

    # Nur artist gesetzt -> Fallback auf Dateiname
    r2 = TrackRecord(path="/sammlung/Viper - Raw Groove.wav", mtime=1.0, size=1, artist="Viper", title="")
    assert _search_query(r2) == "Viper - Raw Groove"

    # Nur title gesetzt -> Fallback auf Dateiname
    r3 = TrackRecord(path="/sammlung/Viper - Raw Groove.wav", mtime=1.0, size=1, artist="", title="Raw Groove")
    assert _search_query(r3) == "Viper - Raw Groove"

    # Weder noch, mit führender ID
    r4 = TrackRecord(path="/sammlung/[12345] Viper - Raw Groove.wav", mtime=1.0, size=1)
    assert _search_query(r4) == "Viper - Raw Groove"


def test_main_cli_dry_run_does_not_write_db(tmp_path, capsys):
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        rec = db.upsert_track(
            TrackRecord(path="/sammlung/Artist A - Title A.wav", mtime=1.0, size=1, status="archive")
        )

    with patch("scripts.link_sources.SoundCloudClient", return_value=FakeSCForMain()):
        ret = main([
            "--likes", "https://soundcloud.com/test/likes",
            "--track-db", str(db_path),
            "-v",
        ])

    assert ret == 0
    captured = capsys.readouterr().out
    assert "LinkStats(records=1, candidates=1, proposals=1, ambiguous=0, applied=0)" in captured

    with TrackDB(db_path) as db:
        updated = db.get_track_by_id(rec.id)
        assert updated.source_url is None or updated.source_url == ""


def test_main_cli_apply_writes_to_db(tmp_path, capsys):
    db_path = tmp_path / "tracks.sqlite"
    report_file = tmp_path / "report.csv"
    with TrackDB(db_path) as db:
        rec = db.upsert_track(
            TrackRecord(path="/sammlung/Artist A - Title A.wav", mtime=1.0, size=1, status="archive")
        )

    with patch("scripts.link_sources.SoundCloudClient", return_value=FakeSCForMain()):
        ret = main([
            "--likes", "https://soundcloud.com/test/likes",
            "--track-db", str(db_path),
            "--report", str(report_file),
            "--apply",
        ])

    assert ret == 0
    captured = capsys.readouterr().out
    assert "LinkStats(records=1, candidates=1, proposals=1, ambiguous=0, applied=1)" in captured
    assert report_file.exists()

    with TrackDB(db_path) as db:
        updated = db.get_track_by_id(rec.id)
        assert updated.source_url == "https://soundcloud.com/uploader/t10"


def test_search_error_is_logged_and_continues(tmp_path):
    class BrokenSearchSC(FakeSC):
        def __init__(self):
            super().__init__({})

        def search_tracks(self, query, limit=5):
            raise SoundCloudError("Suchfehler")

    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.upsert_track(
            TrackRecord(path="/sammlung/Track.wav", mtime=1.0, size=1, status="archive")
        )
        stats = run_link(BrokenSearchSC(), db, search=True)
        assert stats.records == 1
        assert stats.proposals == 0
