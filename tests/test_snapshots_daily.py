"""Eigene Tests für die tägliche Snapshot-Speicherung (Issue #180)."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from sc_digger.db import TrackDB
from tests.test_modes import mk


def _track(i, **kw):
    return replace(mk(i), plays=kw.get("plays", 1000), likes=kw.get("likes", 50),
                   reposts=5, comments=2, tags=kw.get("tags", ["hardtechno"]))


def test_default_recorded_at_uses_utc_now_and_one_row_per_day(tmp_path: Path):
    db_file = tmp_path / "t.sqlite"
    with TrackDB(db_file) as db:
        assert db.record_track_snapshots_daily([_track(1)]) == 1
        assert db.record_track_snapshots_daily([_track(1)]) == 0
        rows = db.db.execute("SELECT recorded_at FROM track_snapshots").fetchall()
    assert len(rows) == 1
    day = datetime.strptime(rows[0]["recorded_at"], "%Y-%m-%d %H:%M:%S")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert day.strftime("%Y-%m-%d") == today


def test_mixed_duplicates_in_one_call(tmp_path: Path):
    db_file = tmp_path / "t.sqlite"
    with TrackDB(db_file) as db:
        db.record_track_snapshots_daily([_track(1)], recorded_at="2026-10-01 08:00:00")
        stored = db.record_track_snapshots_daily(
            [_track(1), _track(2), _track(3)], recorded_at="2026-10-01 20:00:00")
        rows = db.db.execute(
            "SELECT sc_id FROM track_snapshots ORDER BY sc_id").fetchall()
    assert stored == 2
    assert [r["sc_id"] for r in rows] == [1, 2, 3]


def test_tags_with_umlauts_are_not_ascii_escaped(tmp_path: Path):
    db_file = tmp_path / "t.sqlite"
    tags = ["gänsehaut", "müsli"]
    with TrackDB(db_file) as db:
        db.record_track_snapshots_daily(
            [_track(1, tags=tags)], recorded_at="2026-10-01 10:00:00")
        raw = db.db.execute("SELECT tags FROM track_snapshots").fetchone()["tags"]
    assert "gänsehaut" in raw
    assert "\\u" not in raw
    assert json.loads(raw) == tags
