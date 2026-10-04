"""Akzeptanztests Issue A zu #54: Engagement-Snapshots im täglichen Lauf speichern.

Alle Tests laufen ohne Netz. recorded_at wird immer explizit gesetzt.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from sc_digger import main as m
from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.output import State
from tests.test_modes import mk

CFG = Config.load(Path(__file__).resolve().parents[2] / "config.yaml")


def _cfg(tmp_path):
    cfg = Config(dict(CFG.raw))
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    return cfg


def _track(i, plays=1000, likes=50, tags=None):
    return replace(mk(i), plays=plays, likes=likes, reposts=5, comments=2,
                   tags=["hardtechno", "raw"] if tags is None else tags)


def _rows(cfg):
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        return db.db.execute(
            "SELECT sc_id, artist, plays, likes, reposts, comments, tags, recorded_at "
            "FROM track_snapshots ORDER BY id").fetchall()


def test_snapshot_tracks_stores_engagement_and_tags(tmp_path):
    cfg = _cfg(tmp_path)
    t = _track(1, plays=1234, likes=99)
    assert m.snapshot_tracks(cfg, [t], recorded_at="2026-10-01 10:00:00") == 1
    rows = _rows(cfg)
    assert len(rows) == 1
    r = rows[0]
    assert (r["sc_id"], r["artist"], r["plays"], r["likes"], r["reposts"], r["comments"]) == (
        1, t.artist, 1234, 99, 5, 2)
    assert json.loads(r["tags"]) == ["hardtechno", "raw"]
    assert r["recorded_at"] == "2026-10-01 10:00:00"


def test_snapshot_tracks_empty_tags_are_stored_as_null(tmp_path):
    cfg = _cfg(tmp_path)
    m.snapshot_tracks(cfg, [_track(1, tags=[])], recorded_at="2026-10-01 10:00:00")
    assert _rows(cfg)[0]["tags"] is None


def test_snapshot_tracks_keeps_one_snapshot_per_track_and_day(tmp_path):
    cfg = _cfg(tmp_path)
    assert m.snapshot_tracks(cfg, [_track(1, plays=10)], recorded_at="2026-10-01 08:00:00") == 1
    assert m.snapshot_tracks(cfg, [_track(1, plays=20)], recorded_at="2026-10-01 20:00:00") == 0
    rows = _rows(cfg)
    assert len(rows) == 1
    assert rows[0]["plays"] == 10


def test_snapshot_tracks_stores_again_on_next_day(tmp_path):
    cfg = _cfg(tmp_path)
    assert m.snapshot_tracks(cfg, [_track(1)], recorded_at="2026-10-01 23:59:00") == 1
    assert m.snapshot_tracks(cfg, [_track(1)], recorded_at="2026-10-02 00:01:00") == 1
    assert len(_rows(cfg)) == 2


def test_snapshot_tracks_dry_run_writes_nothing(tmp_path):
    cfg = _cfg(tmp_path)
    assert m.snapshot_tracks(cfg, [_track(1)], dry_run=True) == 0
    assert not Path(cfg["state"]["track_db_path"]).exists()


def test_snapshot_tracks_swallows_db_errors(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.raw["state"]["track_db_path"] = str(tmp_path)  # Verzeichnis statt DB-Datei
    assert m.snapshot_tracks(cfg, [_track(1)]) == 0


def test_daily_method_ignores_objects_without_id(tmp_path):
    cfg = _cfg(tmp_path)
    broken = SimpleNamespace(id=None, artist="X", title="t", plays=1, likes=1)
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        stored = db.record_track_snapshots_daily([broken, _track(7)],
                                                 recorded_at="2026-10-01 10:00:00")
    assert stored == 1
    assert [r["sc_id"] for r in _rows(cfg)] == [7]


def test_old_snapshot_calls_keep_working_and_migration_runs_once(tmp_path):
    path = tmp_path / "t.sqlite"
    with TrackDB(path) as db:
        db.record_track_snapshot(sc_id=1, artist="A", likes=3)
    with TrackDB(path) as db:
        versions = [r[0] for r in db.db.execute("SELECT version FROM schema_migrations")]
        row = db.db.execute("SELECT tags, likes FROM track_snapshots").fetchone()
    assert versions.count(6) == 1
    assert row["tags"] is None and row["likes"] == 3


def _patch_discover(monkeypatch, cfg, tracks):
    d = m.Discovery(tracks=tracks, reference_ids=set(), total_sources=1, succeeded=1,
                    failed=[], aborted=None, first_error=None)
    monkeypatch.setattr(m, "SoundCloudClient", lambda: object())
    monkeypatch.setattr(m, "collect_sources", lambda sc, s: d)
    monkeypatch.setattr(m, "filter_sets", lambda t, c: t)
    monkeypatch.setattr(m, "filter_bpm", lambda t, c: t)
    monkeypatch.setattr(m, "score_tracks", lambda t, c: t)
    monkeypatch.setattr(m, "process", lambda t, c, dry_run=False: (list(t), []))
    monkeypatch.setattr(m, "deliver", lambda *a, **k: None)
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg: None)
    monkeypatch.setattr(m, "retry_downloads", lambda sc, cfg: [])
    monkeypatch.setattr(m, "run_intake", lambda cfg: [])
    monkeypatch.setattr(m, "is_sunday", lambda now=None: False)


def test_discover_snapshots_seen_tracks_too_but_not_on_dry_run(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    seen, new = _track(1), _track(2)
    with State(cfg["state"]["db_path"]) as state:
        state.mark_one(seen)
    _patch_discover(monkeypatch, cfg, [seen, new])
    m._discover(cfg, dry_run=True, no_telegram=True)
    assert _rows(cfg) == []
    m._discover(cfg, dry_run=False, no_telegram=True)
    assert sorted(r["sc_id"] for r in _rows(cfg)) == [1, 2]


def test_discover_survives_failing_snapshot(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    _patch_discover(monkeypatch, cfg, [_track(1)])

    def boom(*a, **k):
        raise sqlite3.OperationalError("gesperrt")

    monkeypatch.setattr(TrackDB, "record_track_snapshots_daily", boom)
    assert m._discover(cfg, dry_run=False, no_telegram=True) == 1
