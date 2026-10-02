"""Eigene Tests zu #139: Exploration-Tag-Statistiken (Migration v5, Zählung, Digest).

Keine Netzwerkzugriffe: Suche läuft über Fakes, Telegram wird nie aufgerufen.
"""
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sc_digger import main as m
from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.soundcloud import SoundCloudError
from tests.test_modes import mk

CFG = Config.load(Path(__file__).resolve().parents[1] / "config.yaml")


def _cfg(tmp_path):
    cfg = Config(dict(CFG.raw))
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    return cfg


def test_migration_v5_is_idempotent(tmp_path):
    TrackDB(tmp_path / "t.sqlite").close()
    with TrackDB(tmp_path / "t.sqlite") as db:  # zweiter Start wendet nichts erneut an
        versions = [r[0] for r in db.db.execute("SELECT version FROM schema_migrations")]
    assert versions.count(5) == 1


def test_record_use_updates_timestamps(tmp_path):
    now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    with TrackDB(tmp_path / "t.sqlite") as db:
        db.record_exploration_use("a", True, now=now)
        db.record_exploration_use("a", False, now=now + timedelta(days=1))
        row = db.db.execute("SELECT * FROM exploration_tag_stats WHERE tag='a'").fetchone()
    assert (row["uses"], row["successes"]) == (2, 1)
    assert row["last_success_at"] == "2026-10-01 12:00:00"
    assert row["last_used_at"] == "2026-10-02 12:00:00"


def test_top_respects_min_successes_and_orders(tmp_path):
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    with TrackDB(tmp_path / "t.sqlite") as db:
        for _ in range(3):
            db.record_exploration_use("b", True, now=now)
        for _ in range(2):
            db.record_exploration_use("a", True, now=now)
        db.record_exploration_use("c", True, now=now)
        top = db.top_exploration_tags(days=30, min_successes=2, now=now)
    assert [t["tag"] for t in top] == ["b", "a"]


def test_record_stats_without_tags_never_touches_db(tmp_path):
    cfg = _cfg(tmp_path)
    m.record_exploration_stats(cfg, [], [mk(1)])
    assert not (tmp_path / "tracks.sqlite").exists()


class _SC:
    def __init__(self, fail=()):
        self.fail = set(fail)

    def search_tag(self, tag, age, limit):
        if tag in self.fail:
            raise SoundCloudError("boom")
        return [mk(abs(hash(tag)) % 10_000, title=tag)]

    def user_uploads(self, url, age):
        return []

    def reference_activity(self, url, age, limit=50):
        return []


def _s(tags):
    return {"tags": ["schranz"], "followed_users": [], "reference_accounts": [],
            "exploration_tags": tags, "exploration_probability": 1.0,
            "max_age_days": 14, "limit_per_tag": 10}


def test_collect_sources_reports_only_successful_exploration_tags():
    rng = random.Random()
    rng.random = lambda: 0.0
    d = m.collect_sources(_SC(), _s(["acid techno"]), rng=rng)
    assert d.exploration_used == ["acid techno"]
    d = m.collect_sources(_SC(fail={"acid techno"}), _s(["acid techno"]), rng=rng)
    assert d.exploration_used == []


def test_collect_sources_without_exploration_reports_nothing():
    d = m.collect_sources(_SC(), _s([]))
    assert d.exploration_used == []


def _patch_discover(monkeypatch, tmp_path, calls):
    cfg = _cfg(tmp_path)
    d = m.Discovery(tracks=[], reference_ids=set(), total_sources=1, succeeded=1, failed=[],
                    aborted=None, first_error=None, exploration_used=["acid techno"])
    monkeypatch.setattr(m, "SoundCloudClient", lambda: object())
    monkeypatch.setattr(m, "collect_sources", lambda sc, s: d)
    monkeypatch.setattr(m, "deliver", lambda *a, **k: None)
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg: None)
    monkeypatch.setattr(m, "retry_downloads", lambda sc, cfg: [])
    monkeypatch.setattr(m, "run_intake", lambda cfg: [])
    monkeypatch.setattr(m, "is_sunday", lambda now=None: False)
    monkeypatch.setattr(m, "record_exploration_stats",
                        lambda cfg, used, fresh, now=None: calls.append(used))
    return cfg


def test_discover_records_stats_but_not_on_dry_run(monkeypatch, tmp_path):
    calls = []
    cfg = _patch_discover(monkeypatch, tmp_path, calls)
    m._discover(cfg, dry_run=True, no_telegram=True)
    assert calls == []
    m._discover(cfg, dry_run=False, no_telegram=True)
    assert calls == [["acid techno"]]


def test_discover_survives_failing_stats(monkeypatch, tmp_path):
    cfg = _patch_discover(monkeypatch, tmp_path, [])

    def boom(*a, **k):
        raise sqlite3.OperationalError("gesperrt")
    monkeypatch.setattr(m, "record_exploration_stats", boom)
    assert m._discover(cfg, dry_run=False, no_telegram=True) == 0


def test_digest_section_failure_does_not_break_digest(tmp_path, capsys):
    from sc_digger.stats import send_weekly_digest
    cfg = _cfg(tmp_path)
    cfg.raw["state"]["track_db_path"] = str(tmp_path)  # Verzeichnis statt DB-Datei
    send_weekly_digest(cfg, dry_run=True)
    assert "Woche im Überblick" in capsys.readouterr().out
