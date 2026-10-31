"""Akzeptanztest: On-Demand-Läufe (Bot-Link, playlist, similar) schreiben Engagement-Snapshots."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from sc_digger import main as m
from sc_digger.models import Config
from tests.test_modes import mk

CFG = Config.load(Path(__file__).resolve().parents[2] / "config.yaml")


def _cfg(tmp_path):
    cfg = Config(dict(CFG.raw))
    cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path / "coll"),
                           "inbox_dir": str(tmp_path / "inbox")}
    cfg.raw["state"] = {"db_path": str(tmp_path / "state.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw["search"] = {**CFG["search"], "reference_accounts": [], "followed_users": []}
    return cfg


def _snapshot_ids(tmp_path) -> set[int]:
    path = tmp_path / "tracks.sqlite"
    if not path.exists():
        return set()
    with sqlite3.connect(path) as db:
        try:
            return {r[0] for r in db.execute("SELECT sc_id FROM track_snapshots")}
        except sqlite3.OperationalError:
            return set()


class FakeLinkSC:
    def __init__(self, kind):
        self.kind = kind

    def resolve_kind(self, url):
        return self.kind

    def playlist_tracks(self, url, limit=None):
        return "Liste", [mk(11, playback_count=7), mk(12)]

    def track_station(self, url, limit=50):
        return "A – Seed", [mk(21), mk(22)]


class FakeClient:
    """Ersatz für SoundCloudClient in run_playlist und run_similar."""

    def playlist_tracks(self, url, limit=None):
        return "Liste", [mk(31), mk(32)]

    def likes_of_user(self, url, limit=200):
        return "Likes", [mk(33)]

    def resolve_track(self, url):
        return mk(40)

    def station(self, track_id, limit=50):
        return [mk(40), mk(41), mk(42)]

    def related(self, track_id, limit=50):
        return [mk(40), mk(43)]


def test_run_link_playlist_writes_snapshots(tmp_path):
    m.run_link(_cfg(tmp_path), "u", no_telegram=True, sc=FakeLinkSC("playlist"))
    assert _snapshot_ids(tmp_path) == {11, 12}


def test_run_link_station_writes_snapshots(tmp_path):
    m.run_link(_cfg(tmp_path), "u", no_telegram=True, sc=FakeLinkSC("track"))
    assert _snapshot_ids(tmp_path) == {21, 22}


def test_run_link_dry_run_writes_no_snapshots(tmp_path):
    m.run_link(_cfg(tmp_path), "u", dry_run=True, no_telegram=True, sc=FakeLinkSC("playlist"))
    assert _snapshot_ids(tmp_path) == set()


def test_run_playlist_and_likes_write_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "SoundCloudClient", FakeClient)
    m.run_playlist(_cfg(tmp_path), "u", likes=False, dry_run=False, no_telegram=True)
    assert _snapshot_ids(tmp_path) == {31, 32}
    m.run_playlist(_cfg(tmp_path), "u", likes=True, dry_run=False, no_telegram=True)
    assert _snapshot_ids(tmp_path) == {31, 32, 33}


def test_run_similar_writes_snapshots_without_the_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "SoundCloudClient", FakeClient)
    m.run_similar(_cfg(tmp_path), "u", use_station=True, apply_filter=False, limit=10,
                  dry_run=False, no_telegram=True)
    assert _snapshot_ids(tmp_path) == {41, 42}


def test_run_similar_dry_run_writes_no_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "SoundCloudClient", FakeClient)
    m.run_similar(_cfg(tmp_path), "u", use_station=False, apply_filter=False, limit=10,
                  dry_run=True, no_telegram=True)
    assert _snapshot_ids(tmp_path) == set()
