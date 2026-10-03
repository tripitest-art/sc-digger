"""Eigene Tests: Scout-Fehlermeldung, Digest-Block und Telegram-Fehlerverhalten."""
import logging
from pathlib import Path
from unittest.mock import patch

from sc_digger.db import TrackDB
from sc_digger.models import Config, DownloadKind, Track
from sc_digger.output import TelegramError


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


def _cfg(tmp_path) -> Config:
    cfg = Config.load(Path(__file__).resolve().parents[1] / "config.yaml")
    cfg.raw["state"] = {**cfg.raw.get("state", {}), "track_db_path": str(tmp_path / "t.sqlite")}
    cfg.raw.setdefault("scout", {})
    cfg.raw["scout"].setdefault("bandcamp", {"feeds": []})
    cfg.raw["scout"].setdefault("beatport", {"charts": []})
    cfg.raw.setdefault("download", {})
    cfg.raw["download"]["collection_dir"] = str(tmp_path / "collection")
    (tmp_path / "collection").mkdir(exist_ok=True)
    return cfg


def test_error_message_names_source(tmp_path):
    """Die Fehlermeldung nennt die ausgefallene Quelle."""
    cfg = _cfg(tmp_path)

    with patch("sc_digger.main.fetch_bandcamp_feeds", side_effect=Exception("Bandcamp down")), \
         patch("sc_digger.main.fetch_beatport_charts", return_value=[]), \
         patch("sc_digger.output.telegram_call") as mock_telegram:
        from sc_digger.main import run_scout
        run_scout(cfg, dry_run=False)

    texts = [str(c) for c in mock_telegram.call_args_list]
    assert any("Bandcamp" in t for t in texts)


def test_telegram_error_on_error_message_does_not_abort(tmp_path, caplog):
    """Ein TelegramError beim Fehler-Versand wird geloggt, run_scout bricht nicht ab."""
    cfg = _cfg(tmp_path)

    with patch("sc_digger.main.fetch_bandcamp_feeds", side_effect=Exception("Bandcamp down")), \
         patch("sc_digger.main.fetch_beatport_charts", return_value=[_mk_bc_track(2)]), \
         patch("sc_digger.output.telegram_call", side_effect=TelegramError("kein Token")):
        with caplog.at_level(logging.WARNING, logger="sc_digger"):
            from sc_digger.main import run_scout
            count = run_scout(cfg, dry_run=False)

    assert count == 1
    assert any("fehlgeschlagen" in r.getMessage() for r in caplog.records)
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        assert len(db.get_store_items()) == 1


def test_telegram_error_on_digest_does_not_abort(tmp_path, caplog):
    """Ein TelegramError beim Digest-Versand wird geloggt, run_scout bricht nicht ab."""
    cfg = _cfg(tmp_path)

    with patch("sc_digger.main.fetch_bandcamp_feeds", return_value=[_mk_bc_track(1)]), \
         patch("sc_digger.main.fetch_beatport_charts", return_value=[]), \
         patch("sc_digger.output.telegram_call", side_effect=TelegramError("kein Token")):
        with caplog.at_level(logging.WARNING, logger="sc_digger"):
            from sc_digger.main import run_scout
            count = run_scout(cfg, dry_run=False)

    assert count == 1
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        assert len(db.get_store_items()) == 1


def test_digest_not_sent_without_new_items(tmp_path):
    """Sind keine neuen Items dabei (bereits vorhanden), wird kein Digest gesendet."""
    cfg = _cfg(tmp_path)
    db_path = cfg["state"]["track_db_path"]
    with TrackDB(db_path) as db:
        db.upsert_store_item(sc_id=1, title="BC Track", artist="BC Artist",
                             purchase_url="https://test.bandcamp.com/track/1",
                             purchase_title="Bandcamp")

    with patch("sc_digger.main.fetch_bandcamp_feeds", return_value=[_mk_bc_track(1)]), \
         patch("sc_digger.main.fetch_beatport_charts", return_value=[]), \
         patch("sc_digger.output.telegram_call") as mock_telegram:
        from sc_digger.main import run_scout
        run_scout(cfg, dry_run=False)

    digest_calls = [c for c in mock_telegram.call_args_list
                    if "Neu bei Bandcamp/Beatport" in str(c)]
    assert digest_calls == []


def test_digest_sent_with_new_items(tmp_path):
    """Neue Items landen im Digest-Block; höchstens 5 Einträge."""
    cfg = _cfg(tmp_path)
    tracks = [_mk_bc_track(i, title=f"Fresh {i}", artist=f"Artist {i}") for i in range(1, 8)]

    with patch("sc_digger.main.fetch_bandcamp_feeds", return_value=tracks), \
         patch("sc_digger.main.fetch_beatport_charts", return_value=[]), \
         patch("sc_digger.output.telegram_call") as mock_telegram:
        from sc_digger.main import run_scout
        run_scout(cfg, dry_run=False)

    digest_calls = [c for c in mock_telegram.call_args_list
                    if "Neu bei Bandcamp/Beatport" in str(c)]
    assert len(digest_calls) == 1
    assert "Fresh 1" in str(digest_calls[0])
    assert "Fresh 6" not in str(digest_calls[0])


def test_dry_run_writes_nothing_and_sends_nothing(tmp_path):
    """dry-run schreibt nichts in die DB und ruft telegram_call nicht auf."""
    cfg = _cfg(tmp_path)

    with patch("sc_digger.main.fetch_bandcamp_feeds", return_value=[_mk_bc_track(1)]), \
         patch("sc_digger.main.fetch_beatport_charts", return_value=[]), \
         patch("sc_digger.output.telegram_call") as mock_telegram:
        from sc_digger.main import run_scout
        count = run_scout(cfg, dry_run=True)

    assert count == 0
    mock_telegram.assert_not_called()
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        assert db.get_store_items() == []
