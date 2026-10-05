"""Eigene Tests zur Web-Statusseite (Issue #144), ergänzend zu den Akzeptanztests."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.status import StatusSnapshot, collect_status

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
PASSWORD = "geheim-4711"


def make_cfg(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def client_for(tmp_path, **kw):
    kw.setdefault("password", PASSWORD)
    return TestClient(create_app(make_cfg(tmp_path), **kw))


def test_generated_at_comes_from_now(tmp_path):
    snap = collect_status(make_cfg(tmp_path), now=NOW)
    assert snap.generated_at == NOW.strftime("%Y-%m-%d %H:%M:%S")


def test_days_defaults_to_seven(tmp_path):
    snap = collect_status(make_cfg(tmp_path), now=NOW)
    assert snap.stats.days == 7


def test_index_without_runs_shows_hint(tmp_path):
    r = client_for(tmp_path).get("/", auth=("sc", PASSWORD))
    assert r.status_code == 200
    assert "Noch kein Lauf" in r.text


def test_snapshot_to_dict_has_all_fields(tmp_path):
    snap = collect_status(make_cfg(tmp_path), now=NOW)
    data = snap.to_dict()
    assert set(data) == {
        "generated_at", "runs", "last_discover", "last_discover_age_hours",
        "healthy", "alert_active", "inbox_total", "stats",
    }
    json.dumps(data)


def test_healthz_works_even_anonymous_disabled(tmp_path):
    c = TestClient(create_app(make_cfg(tmp_path), password=PASSWORD))
    assert c.get("/healthz").status_code == 200


def test_index_renders_base_layout_and_navigation(tmp_path):
    r = client_for(tmp_path).get("/", auth=("sc", PASSWORD))
    assert r.status_code == 200
    assert "site-header" in r.text
    assert "brand-title" in r.text
    assert "nav-tabs" in r.text
    assert "--bg-app" in r.text
    assert "status-pill" in r.text

