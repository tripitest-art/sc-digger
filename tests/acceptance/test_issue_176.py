"""Akzeptanztests: run_scout überspringt Duplikate aus der Sammlung."""
from pathlib import Path
from unittest.mock import patch

import pytest

from sc_digger.db import TrackDB
from sc_digger.models import Config, DownloadKind, Track


def _mk_bc_track(sc_id: int, title: str = "BC Track", artist: str = "BC Artist") -> Track:
    return Track(
        id=sc_id, title=title, url=f"https://test.bandcamp.com/track/{sc_id}",
        artist=artist, artist_url="", created_at="2025-09-29T10:00:00Z",
        duration_ms=300000, genre="Hard Techno", tags=[], description="",
        bpm=None, plays=0, likes=0, reposts=0, comments=0,
        downloadable=False, has_downloads_left=False,
        purchase_url=f"https://test.bandcamp.com/track/{sc_id}",
        purchase_title="Bandcamp", download_kind=DownloadKind.STORE,
    )


def _mk_bp_track(sc_id: int, title: str = "BP Track", artist: str = "BP Artist") -> Track:
    return Track(
        id=sc_id, title=title, url=f"https://www.beatport.com/track/slug/{sc_id}",
        artist=artist, artist_url="", created_at="2025-09-29T10:00:00Z",
        duration_ms=300000, genre="Hard Techno", tags=[], description="",
        bpm=None, plays=0, likes=0, reposts=0, comments=0,
        downloadable=False, has_downloads_left=False,
        purchase_url=f"https://www.beatport.com/track/slug/{sc_id}",
        purchase_title="Beatport", download_kind=DownloadKind.STORE,
    )


def _cfg(tmp_path, collection_dir: str) -> Config:
    cfg = Config.load(Path(__file__).resolve().parents[2] / "config.yaml")
    cfg.raw["state"] = {**cfg.raw.get("state", {}), "track_db_path": str(tmp_path / "t.sqlite")}
    cfg.raw.setdefault("scout", {})
    cfg.raw["scout"].setdefault("bandcamp", {"feeds": []})
    cfg.raw["scout"].setdefault("beatport", {"charts": []})
    cfg.raw.setdefault("download", {})
    cfg.raw["download"]["collection_dir"] = collection_dir
    return cfg


def test_run_scout_skips_track_on_disk(tmp_path):
    """Ein Track, dessen normalisierter Titel in der Sammlung liegt, wird nicht gespeichert."""
    coll = tmp_path / "collection"
    coll.mkdir()
    (coll / "BC Artist - BC Track.wav").write_bytes(b"")

    cfg = _cfg(tmp_path, str(coll))

    with patch("sc_digger.main.fetch_bandcamp_feeds", return_value=[_mk_bc_track(1)]), \
         patch("sc_digger.main.fetch_beatport_charts", return_value=[]):

        from sc_digger.main import run_scout
        count = run_scout(cfg, dry_run=False)

    assert count == 0

    with TrackDB(cfg["state"]["track_db_path"]) as db:
        items = db.get_store_items()
    assert len(items) == 0


def test_run_scout_stores_non_duplicates(tmp_path):
    """Nur Tracks ohne Duplikat in der Sammlung landen in store_items."""
    coll = tmp_path / "collection"
    coll.mkdir()
    (coll / "Existing Artist - Existing Track.flac").write_bytes(b"")

    cfg = _cfg(tmp_path, str(coll))

    with patch("sc_digger.main.fetch_bandcamp_feeds",
               return_value=[_mk_bc_track(1, title="Existing Track", artist="Existing Artist"),
                             _mk_bc_track(2, title="Fresh Rave", artist="Fresh Quartz")]), \
         patch("sc_digger.main.fetch_beatport_charts", return_value=[]):

        from sc_digger.main import run_scout
        count = run_scout(cfg, dry_run=False)

    assert count == 1

    with TrackDB(cfg["state"]["track_db_path"]) as db:
        items = db.get_store_items()
    titles = {i["title"] for i in items}
    assert titles == {"Fresh Rave"}


def test_run_scout_dry_run_stores_nothing(tmp_path):
    """Im dry-run-Modus wird nichts geschrieben, auch wenn Duplikate erkannt werden."""
    coll = tmp_path / "collection"
    coll.mkdir()
    (coll / "BC Artist - BC Track.wav").write_bytes(b"")

    cfg = _cfg(tmp_path, str(coll))

    with patch("sc_digger.main.fetch_bandcamp_feeds", return_value=[_mk_bc_track(1)]), \
         patch("sc_digger.main.fetch_beatport_charts", return_value=[]):

        from sc_digger.main import run_scout
        count = run_scout(cfg, dry_run=True)

    assert count == 0
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        items = db.get_store_items()
    assert len(items) == 0


def test_run_scout_counts_duplicates_across_sources(tmp_path):
    """Duplikate aus beiden Scouts werden gezählt und übersprungen."""
    coll = tmp_path / "collection"
    coll.mkdir()
    (coll / "Both Artist - Both Track.wav").write_bytes(b"")

    cfg = _cfg(tmp_path, str(coll))

    with patch("sc_digger.main.fetch_bandcamp_feeds",
               return_value=[_mk_bc_track(1, title="Both Track", artist="Both Artist"),
                             _mk_bc_track(2, title="Fresh BC", artist="Fresh BC")]), \
         patch("sc_digger.main.fetch_beatport_charts",
               return_value=[_mk_bp_track(100, title="Both Track", artist="Both Artist"),
                             _mk_bp_track(200, title="Fresh BP", artist="Fresh BP")]):

        from sc_digger.main import run_scout
        count = run_scout(cfg, dry_run=False)

    assert count == 2

    with TrackDB(cfg["state"]["track_db_path"]) as db:
        items = db.get_store_items()
    titles = {i["title"] for i in items}
    assert titles == {"Fresh BC", "Fresh BP"}
