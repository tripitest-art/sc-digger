"""Unit-Tests für Engagement-Snapshots in On-Demand-Läufen (#437)."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sc_digger import main as m
from sc_digger.models import Config
from tests.acceptance.test_issue_437 import FakeClient, FakeLinkSC
from tests.test_modes import mk

CFG = Config.load(Path(__file__).resolve().parents[1] / "config.yaml")


def _cfg(tmp_path: Path) -> Config:
    cfg = Config(dict(CFG.raw))
    cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path / "coll"),
                           "inbox_dir": str(tmp_path / "inbox")}
    cfg.raw["state"] = {"db_path": str(tmp_path / "state.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw["search"] = {**CFG["search"], "reference_accounts": [], "followed_users": []}
    return cfg


def test_snapshot_fehler_blockiert_den_lauf_nicht(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Wenn das Schreiben von Snapshots fehlschlägt, fängt snapshot_tracks den Fehler ab und der Lauf geht normal weiter."""
    from sc_digger.db import TrackDB
    monkeypatch.setattr(TrackDB, "record_track_snapshots_daily", MagicMock(side_effect=RuntimeError("DB gesperrt")))
    cfg = _cfg(tmp_path)

    # run_link wirft nicht und liefert das normale Ergebnis
    source, fresh, dupes = m.run_link(cfg, "https://soundcloud.com/user/sets/playlist",
                                      no_telegram=True, sc=FakeLinkSC("playlist"))
    assert source == "Playlist: Liste"
    assert fresh + dupes == 2


def test_zweiter_lauf_am_selben_tag_schreibt_keinen_zweiten_snapshot(tmp_path: Path):
    """record_track_snapshots_daily stellt sicher, dass am selben Tag nur 1 Snapshot je Track existiert."""
    cfg = _cfg(tmp_path)
    db_path = tmp_path / "tracks.sqlite"

    m.run_link(cfg, "u", no_telegram=True, sc=FakeLinkSC("playlist"))
    with sqlite3.connect(db_path) as db:
        count_first = db.execute("SELECT COUNT(*) FROM track_snapshots").fetchone()[0]

    assert count_first == 2

    # Erneuter Lauf am selben Tag
    m.run_link(cfg, "u", no_telegram=True, sc=FakeLinkSC("playlist"))
    with sqlite3.connect(db_path) as db:
        count_second = db.execute("SELECT COUNT(*) FROM track_snapshots").fetchone()[0]

    assert count_second == 2
