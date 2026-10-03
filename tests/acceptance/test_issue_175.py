"""Akzeptanztests: Scout-Fehlermeldung, Digest-Block, dry-run ohne Senden."""
from pathlib import Path
from unittest.mock import patch

import pytest

from sc_digger.db import TrackDB
from sc_digger.models import Config, DownloadKind, Track


def _mk_bc_track(sc_id: int, title: str = "BC Track", artist: str = "BC Artist") -> Track:
    return Track(
        id=sc_id, title=title, url=f"https://test.bandcamp.com/track/{sc_id}",
        artist=artist, artist_url="", created_at="2025-09-30T10:00:00Z",
        duration_ms=300000, genre="Hard Techno", tags=[], description="",
        bpm=None, plays=0, likes=0, reposts=0, comments=0,
        downloadable=False, has_downloads_left=False,
        purchase_url=f"https://test.bandcamp.com/track/{sc_id}",
        purchase_title="Bandcamp", download_kind=DownloadKind.STORE,
    )


def _mk_bp_track(sc_id: int, title: str = "BP Track", artist: str = "BP Artist") -> Track:
    return Track(
        id=sc_id, title=title, url=f"https://www.beatport.com/track/slug/{sc_id}",
        artist=artist, artist_url="", created_at="2025-09-30T10:00:00Z",
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
    cfg.raw.setdefault("download", {})
    cfg.raw["download"]["collection_dir"] = str(tmp_path / "collection")
    (tmp_path / "collection").mkdir()
    return cfg


def test_run_scout_sends_error_message_on_scout_failure(tmp_path):
    """Fehler eines Scouts lösen eine Telegram-Meldung aus, schlagen aber nicht fehl."""
    cfg = _cfg(tmp_path)

    with patch("sc_digger.main.fetch_bandcamp_feeds", side_effect=Exception("Bandcamp down")), \
         patch("sc_digger.main.fetch_beatport_charts", return_value=[]), \
         patch("sc_digger.output.telegram_call") as mock_telegram:

        from sc_digger.main import run_scout
        run_scout(cfg, dry_run=False)

    assert mock_telegram.called


def test_run_scout_sends_digest_block_with_new_items(tmp_path):
    """Nach dem Lauf wird der Digest-Block mit höchstens 5 neuen Einträgen gesendet."""
    cfg = _cfg(tmp_path)
    tracks = [_mk_bc_track(i, title=f"Fresh {i}", artist=f"Artist {i}") for i in range(1, 7)]

    with patch("sc_digger.main.fetch_bandcamp_feeds", return_value=tracks), \
         patch("sc_digger.main.fetch_beatport_charts", return_value=[]), \
         patch("sc_digger.output.telegram_call") as mock_telegram:

        from sc_digger.main import run_scout
        run_scout(cfg, dry_run=False)

    digest_calls = [c for c in mock_telegram.call_args_list
                    if "Neu bei Bandcamp/Beatport" in str(c)]
    assert len(digest_calls) == 1
    block = str(digest_calls[0])
    assert block.count("Fresh") == 5  # höchstens 5 Einträge


def test_run_scout_dry_run_sends_nothing(tmp_path):
    """Im dry-run-Modus werden weder Meldungen gesendet noch Daten geschrieben."""
    cfg = _cfg(tmp_path)

    with patch("sc_digger.main.fetch_bandcamp_feeds", return_value=[_mk_bc_track(1)]), \
         patch("sc_digger.main.fetch_beatport_charts", return_value=[]), \
         patch("sc_digger.output.telegram_call") as mock_telegram:

        from sc_digger.main import run_scout
        run_scout(cfg, dry_run=True)

    mock_telegram.assert_not_called()
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        assert len(db.get_store_items()) == 0


def test_entrypoint_schedules_scout_separately_from_discover():
    """Der Scout bekommt einen eigenen Cron-Eintrag; der discover-Lauf im Dockerfile bleibt unberührt."""
    root = Path(__file__).resolve().parents[2]
    entry = (root / "entrypoint.sh").read_text(encoding="utf-8")
    assert "python -m sc_digger.main scout" in entry
    assert "0 18 * * *" in entry
    docker = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "30 7 * * *" in docker
    assert "main scout" not in docker
