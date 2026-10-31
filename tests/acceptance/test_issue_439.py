"""Akzeptanztest: Ein Track-Link im Bot verarbeitet den Track selbst, nicht nur seine Station."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from sc_digger import main as m
from sc_digger.models import Config
from tests.test_modes import FakeSC, raw_track

CFG = Config.load(Path(__file__).resolve().parents[2] / "config.yaml")
SEED_URL = "https://soundcloud.com/jezzn/exitus"
STATION = "/system-playlists/soundcloud:system-playlists:track-stations:99"


def _cfg(tmp_path):
    cfg = Config(dict(CFG.raw))
    cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path / "coll"),
                           "inbox_dir": str(tmp_path / "inbox")}
    cfg.raw["state"] = {"db_path": str(tmp_path / "state.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw["search"] = {**CFG["search"], "reference_accounts": [], "followed_users": []}
    return cfg


def _sc(station_tracks=None, *, seed_downloadable=True):
    seed = raw_track(99, title="EXITUS (Halloween Schranz Intro) [FREE DL]",
                     user={"username": "Jezzn", "permalink_url": "https://soundcloud.com/jezzn"},
                     downloadable=seed_downloadable, has_downloads_left=True)
    if station_tracks is None:
        station_tracks = [raw_track(1), raw_track(99), raw_track(2)]
    return FakeSC({SEED_URL: seed, STATION: {"tracks": station_tracks},
                   "/tracks/99/related": {"collection": []}})


def test_track_station_starts_with_the_seed_and_drops_its_repeat():
    label, tracks = _sc().track_station(SEED_URL, limit=50)
    assert label == "Jezzn – EXITUS (Halloween Schranz Intro) [FREE DL]"
    assert [t.id for t in tracks] == [99, 1, 2]
    assert tracks[0].downloadable is True
    assert tracks[0].artist == "Jezzn"


def test_limit_applies_to_the_station_only():
    _, tracks = _sc().track_station(SEED_URL, limit=1)
    assert [t.id for t in tracks] == [99, 1]


def test_empty_station_leaves_only_the_seed():
    _, tracks = _sc(station_tracks=[]).track_station(SEED_URL, limit=50)
    assert [t.id for t in tracks] == [99]


def test_run_link_processes_the_seed_and_tries_to_download_it(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUNDCLOUD_AUTH_TOKEN", "test-token")
    attempted = []

    def fake_download(t, inbox, auth_token=None):
        attempted.append(t.id)
        return None

    monkeypatch.setattr(m, "download_native", fake_download)
    cfg = _cfg(tmp_path)
    source, fresh, dupes = m.run_link(cfg, SEED_URL, no_telegram=True, sc=_sc())
    assert source == "Station zu: Jezzn – EXITUS (Halloween Schranz Intro) [FREE DL]"
    assert attempted == [99]
    assert fresh + dupes == 3
    with sqlite3.connect(tmp_path / "state.sqlite") as db:
        seen = {r[0] for r in db.execute("SELECT id FROM seen")}
    assert 99 in seen


def test_run_link_does_not_download_a_seed_that_is_not_downloadable(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUNDCLOUD_AUTH_TOKEN", "test-token")
    attempted = []
    monkeypatch.setattr(m, "download_native", lambda t, inbox, auth_token=None: attempted.append(t.id))
    m.run_link(_cfg(tmp_path), SEED_URL, no_telegram=True, sc=_sc(seed_downloadable=False))
    assert attempted == []
