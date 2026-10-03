"""Akzeptanztests: CLI-Modus scout – Bandcamp + Beatport → store_items."""
from pathlib import Path
from unittest.mock import patch

import pytest

from sc_digger.db import TrackDB
from sc_digger.models import Config, DownloadKind, Track


def _mk_bc_track(sc_id: int = 1) -> Track:
    return Track(
        id=sc_id, title="BC Track", url=f"https://test.bandcamp.com/track/{sc_id}",
        artist="BC Artist", artist_url="", created_at="2025-09-29T10:00:00Z",
        duration_ms=300000, genre="Hard Techno", tags=[], description="",
        bpm=None, plays=0, likes=0, reposts=0, comments=0,
        downloadable=False, has_downloads_left=False,
        purchase_url=f"https://test.bandcamp.com/track/{sc_id}",
        purchase_title="Bandcamp", download_kind=DownloadKind.STORE,
    )


def _mk_bp_track(sc_id: int = 100) -> Track:
    return Track(
        id=sc_id, title="BP Track", url=f"https://www.beatport.com/track/slug/{sc_id}",
        artist="BP Artist", artist_url="", created_at="2025-09-29T10:00:00Z",
        duration_ms=300000, genre="Hard Techno", tags=[], description="",
        bpm=None, plays=0, likes=0, reposts=0, comments=0,
        downloadable=False, has_downloads_left=False,
        purchase_url=f"https://www.beatport.com/track/slug/{sc_id}",
        purchase_title="Beatport", download_kind=DownloadKind.STORE,
    )


def _cfg(tmp_path) -> Config:
    cfg = Config.load(Path(__file__).resolve().parents[2] / "config.yaml")
    cfg.raw["state"] = {**cfg.raw.get("state", {}), "track_db_path": str(tmp_path / "t.sqlite")}
    cfg.raw.setdefault("scout", {})
    cfg.raw["scout"].setdefault("bandcamp", {"feeds": []})
    cfg.raw["scout"].setdefault("beatport", {"charts": []})
    return cfg


def test_scout_cli_parses():
    """python -m sc_digger.main scout wird als Modus erkannt."""
    import sys

    from sc_digger import main as m
    from sc_digger.models import Config

    cfg = Config.load(Path(__file__).resolve().parents[2] / "config.yaml")

    with patch.object(m, "run_scout") as mock_run:
        with patch.object(sys, "argv", ["sc_digger", "scout", "--dry-run"]):
            try:
                m.cli()
            except SystemExit:
                pass

    mock_run.assert_called_once()


def test_run_scout_upserts_store_items(tmp_path):
    """run_scout() schreibt Tracks beider Scouts in store_items."""
    cfg = _cfg(tmp_path)

    with patch("sc_digger.main.fetch_bandcamp_feeds", return_value=[_mk_bc_track(1), _mk_bc_track(2)]), \
         patch("sc_digger.main.fetch_beatport_charts", return_value=[_mk_bp_track(100)]):

        from sc_digger.main import run_scout
        count = run_scout(cfg, dry_run=False)

    assert count == 3

    with TrackDB(cfg["state"]["track_db_path"]) as db:
        items = db.get_store_items()
    assert len(items) == 3
    titles = {i["title"] for i in items}
    assert titles == {"BC Track", "BP Track"}


def test_run_scout_dry_run_no_db_write(tmp_path):
    """Im dry-run-Modus werden keine Store-Items geschrieben."""
    cfg = _cfg(tmp_path)

    with patch("sc_digger.main.fetch_bandcamp_feeds", return_value=[_mk_bc_track(1)]), \
         patch("sc_digger.main.fetch_beatport_charts", return_value=[]):

        from sc_digger.main import run_scout
        count = run_scout(cfg, dry_run=True)

    assert count == 0
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        items = db.get_store_items()
    assert len(items) == 0


def test_run_scout_one_scout_fails_other_succeeds(tmp_path):
    """Wenn ein Scout fehlschlägt, werden die Daten des anderen trotzdem geschrieben."""
    cfg = _cfg(tmp_path)

    with patch("sc_digger.main.fetch_bandcamp_feeds", side_effect=Exception("Bandcamp down")), \
         patch("sc_digger.main.fetch_beatport_charts", return_value=[_mk_bp_track(100), _mk_bp_track(200)]):

        from sc_digger.main import run_scout
        count = run_scout(cfg, dry_run=False)

    assert count == 2
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        items = db.get_store_items()
    titles = {i["title"] for i in items}
    assert titles == {"BP Track"}
