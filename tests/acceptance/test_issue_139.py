"""Akzeptanztests: Exploration-Tag-Statistiken & Digest-Vorschlag (Issue #139)."""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sc_digger.db import TrackDB
from sc_digger.main import record_exploration_stats
from sc_digger.models import Config
from sc_digger.stats import send_weekly_digest
from tests.test_modes import mk

ROOT = Path(__file__).resolve().parents[2]
CFG = Config.load(ROOT / "config.yaml")


def _ts(days_ago=0):
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def _cfg(tmp_path):
    cfg = Config(dict(CFG.raw))
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def _rows(tmp_path):
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        return {r[0]: (r[1], r[2]) for r in db.execute(
            "SELECT tag, uses, successes FROM exploration_tag_stats")}


def test_migration_creates_table_and_counts(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.record_exploration_use("warehouse techno", True, now=_ts(1))
        db.record_exploration_use("warehouse techno", True, now=_ts(0))
        db.record_exploration_use("acid techno", False, now=_ts(0))
        top = db.top_exploration_tags(days=30, min_successes=2)
        assert len(top) == 1
        assert top[0]["tag"] == "warehouse techno"
        assert top[0]["successes"] == 2
        assert top[0]["uses"] == 2
        assert top[0]["last_success_at"]
        assert db.top_exploration_tags(days=30, min_successes=3) == []
    assert _rows(tmp_path) == {"warehouse techno": (2, 2), "acid techno": (1, 0)}


def test_top_exploration_tags_respects_time_window(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.record_exploration_use("old tag", True, now=_ts(40))
        db.record_exploration_use("old tag", True, now=_ts(39))
        assert db.top_exploration_tags(days=30, min_successes=2) == []
        assert len(db.top_exploration_tags(days=60, min_successes=2)) == 1


def test_record_exploration_stats_counts_uses_and_fresh_hits(tmp_path):
    cfg = _cfg(tmp_path)
    hit = mk(1, title="Treffer")
    hit.exploration_tag = "warehouse techno"
    regular = mk(2, title="Regulaer")
    record_exploration_stats(cfg, ["warehouse techno", "acid techno"], [hit, regular])
    assert _rows(tmp_path) == {"warehouse techno": (1, 1), "acid techno": (1, 0)}


def test_record_exploration_stats_without_tags_writes_nothing(tmp_path):
    cfg = _cfg(tmp_path)
    record_exploration_stats(cfg, [], [mk(1)])
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        assert db.top_exploration_tags(days=30, min_successes=1) == []
    assert _rows(tmp_path) == {}


def test_digest_proposes_successful_tag(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        db.record_exploration_use("warehouse techno", True, now=_ts(2))
        db.record_exploration_use("warehouse techno", True, now=_ts(1))
        db.record_exploration_use("acid techno", False, now=_ts(1))
    send_weekly_digest(cfg, dry_run=True)
    out = capsys.readouterr().out
    assert "warehouse techno" in out
    assert "erfolgreiche Treffer" in out
    assert "search.tags" in out
    assert "acid techno" not in out


def test_digest_without_hits_has_no_exploration_section(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        db.record_exploration_use("acid techno", False, now=_ts(1))
    send_weekly_digest(cfg, dry_run=True)
    out = capsys.readouterr().out
    assert "🔍" not in out
    assert "search.tags" not in out
