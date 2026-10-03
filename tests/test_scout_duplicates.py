"""Eigene Tests: run_scout markiert, zählt und loggt Duplikate gegen die Sammlung."""
import logging
from pathlib import Path
from unittest.mock import patch

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


def _cfg(tmp_path, collection_dir: str | None) -> Config:
    cfg = Config.load(Path(__file__).resolve().parents[1] / "config.yaml")
    cfg.raw["state"] = {**cfg.raw.get("state", {}), "track_db_path": str(tmp_path / "t.sqlite")}
    cfg.raw.setdefault("scout", {})
    cfg.raw["scout"].setdefault("bandcamp", {"feeds": []})
    cfg.raw["scout"].setdefault("beatport", {"charts": []})
    cfg.raw.setdefault("download", {})
    if collection_dir is None:
        cfg.raw["download"].pop("collection_dir", None)
    else:
        cfg.raw["download"]["collection_dir"] = collection_dir
    return cfg


def test_run_scout_sets_duplicate_of(tmp_path):
    """Auf erkannten Duplikaten wird duplicate_of gesetzt; Nicht-Duplikate bleiben leer."""
    coll = tmp_path / "collection"
    coll.mkdir()
    (coll / "Dupe Artist - Dupe Track.wav").write_bytes(b"")

    cfg = _cfg(tmp_path, str(coll))

    with patch("sc_digger.main.fetch_bandcamp_feeds",
               return_value=[_mk_bc_track(1, title="Dupe Track", artist="Dupe Artist"),
                             _mk_bc_track(2, title="Fresh Rave", artist="Fresh Quartz")]), \
         patch("sc_digger.main.fetch_beatport_charts", return_value=[]):
        from sc_digger.main import run_scout
        run_scout(cfg, dry_run=False)

    with TrackDB(cfg["state"]["track_db_path"]) as db:
        items = db.get_store_items()
    assert {i["sc_id"] for i in items} == {2}


def test_run_scout_missing_collection_dir_warns_and_stores_all(tmp_path, caplog):
    """Fehlt der Sammlungsordner, warnt Collection und es wird nicht abgeglichen."""
    cfg = _cfg(tmp_path, str(tmp_path / "does-not-exist"))

    with patch("sc_digger.main.fetch_bandcamp_feeds", return_value=[_mk_bc_track(1)]), \
         patch("sc_digger.main.fetch_beatport_charts", return_value=[]):
        with caplog.at_level(logging.WARNING, logger="sc_digger.collection"):
            from sc_digger.main import run_scout
            count = run_scout(cfg, dry_run=False)

    assert count == 1
    assert any("nicht gefunden" in r.getMessage() for r in caplog.records)
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        items = db.get_store_items()
    assert len(items) == 1


def test_run_scout_logs_every_duplicate(tmp_path, caplog):
    """Jedes Duplikat wird mit Titel und Fundstelle geloggt."""
    coll = tmp_path / "collection"
    coll.mkdir()
    (coll / "Both Artist - Both Track.wav").write_bytes(b"")

    cfg = _cfg(tmp_path, str(coll))

    with patch("sc_digger.main.fetch_bandcamp_feeds",
               return_value=[_mk_bc_track(1, title="Both Track", artist="Both Artist"),
                             _mk_bc_track(2, title="Fresh Rave", artist="Fresh Quartz")]), \
         patch("sc_digger.main.fetch_beatport_charts", return_value=[]):
        with caplog.at_level(logging.INFO, logger="sc_digger"):
            from sc_digger.main import run_scout
            run_scout(cfg, dry_run=False)

    dupe_logs = [r for r in caplog.records if "Duplikat übersprungen" in r.getMessage()]
    assert len(dupe_logs) == 1
    assert "Both Artist" in dupe_logs[0].getMessage()
    assert "Both Track" in dupe_logs[0].getMessage()
