"""Akzeptanztests: Web-Status-Seite, nur lesend (Issue #<N>)."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.health import Health
from sc_digger.models import Config
from sc_digger.stats import DigestStats
from sc_digger.web.app import create_app
from sc_digger.web.status import StatusSnapshot, collect_status

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
PASSWORD = "geheim-4711"


def stamp(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")


def make_cfg(tmp_path, **health):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    cfg.raw["health"] = {"alert_after_bad_runs": 2, "max_hours_since_run": 36, **health}
    return cfg


def add_run(tmp_path, hours_ago, *, mode="discover", ok=True, found=100, error=None):
    with Health(tmp_path / "seen.sqlite") as h:
        h.db.execute("INSERT INTO runs (mode, finished_at, ok, found, error) VALUES (?,?,?,?,?)",
                     (mode, stamp(hours_ago), int(ok), found, error))
        h.db.commit()


def set_alert(tmp_path, active=True):
    with Health(tmp_path / "seen.sqlite") as h:
        h.db.execute("INSERT OR REPLACE INTO health_state (key, value) VALUES ('alert:discover', ?)",
                     ("1" if active else "0",))
        h.db.commit()


def add_track(tmp_path, name, status):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.upsert_track(TrackRecord(path=f"/music/{name}.wav", mtime=1.0, size=10, status=status, artist="A"))


@pytest.fixture(autouse=True)
def no_local_env(monkeypatch):
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)


# ------------------------------------------------------------------ collect_status
def test_empty_state_gives_empty_snapshot_and_creates_no_files(tmp_path):
    snap = collect_status(make_cfg(tmp_path), now=NOW)
    assert isinstance(snap, StatusSnapshot)
    assert snap.runs == [] and snap.last_discover is None
    assert snap.last_discover_age_hours is None
    assert snap.healthy is False and snap.alert_active is False
    assert snap.inbox_total == 0
    assert isinstance(snap.stats, DigestStats)
    assert list(tmp_path.iterdir()) == []


def test_recent_successful_run_is_healthy(tmp_path):
    add_run(tmp_path, 2, found=120)
    snap = collect_status(make_cfg(tmp_path), now=NOW)
    assert snap.last_discover.ok is True and snap.last_discover.found == 120
    assert snap.last_discover.finished_at == stamp(2)
    assert snap.last_discover_age_hours == pytest.approx(2.0, abs=0.1)
    assert snap.healthy is True


def test_old_run_is_not_healthy(tmp_path):
    add_run(tmp_path, 40)
    snap = collect_status(make_cfg(tmp_path), now=NOW)
    assert snap.last_discover_age_hours == pytest.approx(40.0, abs=0.1)
    assert snap.healthy is False


def test_age_limit_comes_from_config(tmp_path):
    add_run(tmp_path, 40)
    assert collect_status(make_cfg(tmp_path, max_hours_since_run=48), now=NOW).healthy is True


def test_failed_run_is_not_healthy_and_keeps_error(tmp_path):
    add_run(tmp_path, 1, ok=False, found=0, error="client_id nicht ermittelbar")
    snap = collect_status(make_cfg(tmp_path), now=NOW)
    assert snap.last_discover.ok is False
    assert snap.last_discover.error == "client_id nicht ermittelbar"
    assert snap.healthy is False


def test_active_alert_is_reported_and_not_healthy(tmp_path):
    add_run(tmp_path, 1)
    set_alert(tmp_path, True)
    snap = collect_status(make_cfg(tmp_path), now=NOW)
    assert snap.alert_active is True and snap.healthy is False
    set_alert(tmp_path, False)
    assert collect_status(make_cfg(tmp_path), now=NOW).alert_active is False


def test_runs_are_newest_first_limited_to_ten_and_include_all_modes(tmp_path):
    for i in range(12):
        add_run(tmp_path, 100 - i, mode="discover" if i % 2 == 0 else "playlist")
    snap = collect_status(make_cfg(tmp_path), now=NOW)
    assert len(snap.runs) == 10
    assert snap.runs[0].finished_at == stamp(89)          # zuletzt eingefügt
    assert {r.mode for r in snap.runs} == {"discover", "playlist"}
    assert snap.last_discover.mode == "discover"


def test_last_discover_ignores_other_modes(tmp_path):
    add_run(tmp_path, 30, mode="discover", found=50)
    add_run(tmp_path, 1, mode="playlist", found=7)
    snap = collect_status(make_cfg(tmp_path), now=NOW)
    assert snap.last_discover.found == 50


def test_error_text_is_redacted(tmp_path):
    token = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
    add_run(tmp_path, 1, ok=False, error=f"HTTPError https://api.telegram.org/bot123456789:{token}/getMe")
    snap = collect_status(make_cfg(tmp_path), now=NOW)
    assert token not in snap.last_discover.error
    assert token not in json.dumps(snap.to_dict())


def test_inbox_total_counts_only_inbox_tracks(tmp_path):
    add_track(tmp_path, "a", "inbox")
    add_track(tmp_path, "b", "inbox")
    add_track(tmp_path, "c", "rejected")
    add_track(tmp_path, "d", "archive")
    assert collect_status(make_cfg(tmp_path), now=NOW).inbox_total == 2


def test_days_is_passed_to_stats(tmp_path):
    assert collect_status(make_cfg(tmp_path), days=3, now=NOW).stats.days == 3


def test_to_dict_is_json_serializable(tmp_path):
    add_run(tmp_path, 2)
    add_track(tmp_path, "a", "inbox")
    data = collect_status(make_cfg(tmp_path), now=NOW).to_dict()
    json.dumps(data)
    for key in ("generated_at", "runs", "last_discover", "last_discover_age_hours",
                "healthy", "alert_active", "inbox_total", "stats"):
        assert key in data


def test_collect_status_never_writes(tmp_path):
    add_run(tmp_path, 2)
    add_track(tmp_path, "a", "inbox")
    def main_files():
        # -wal/-shm entstehen beim Lesen einer WAL-Datenbank und gehören nicht zum Inhalt
        return {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.suffix == ".sqlite"}

    before = main_files()
    assert before
    collect_status(make_cfg(tmp_path), now=NOW)
    assert main_files() == before


def test_broken_database_does_not_raise(tmp_path):
    (tmp_path / "seen.sqlite").write_bytes(b"das ist keine sqlite-datei" * 10)
    (tmp_path / "tracks.sqlite").write_bytes(b"auch nicht" * 10)
    snap = collect_status(make_cfg(tmp_path), now=NOW)
    assert snap.runs == [] and snap.healthy is False


# ------------------------------------------------------------------ Web-App
def client_for(tmp_path, **kw):
    kw.setdefault("password", PASSWORD)
    return TestClient(create_app(make_cfg(tmp_path), **kw))


def test_create_app_requires_password_unless_anonymous_allowed(tmp_path):
    with pytest.raises(ValueError):
        create_app(make_cfg(tmp_path), password=None)
    with pytest.raises(ValueError):
        create_app(make_cfg(tmp_path), password="")
    c = TestClient(create_app(make_cfg(tmp_path), password=None, allow_anonymous=True))
    assert c.get("/").status_code == 200


def test_healthz_needs_no_auth(tmp_path):
    r = client_for(tmp_path).get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_pages_require_auth(tmp_path):
    c = client_for(tmp_path)
    for path in ("/", "/api/status"):
        r = c.get(path)
        assert r.status_code == 401
        assert r.headers["www-authenticate"].lower().startswith("basic")
        assert c.get(path, auth=("sc", "falsch")).status_code == 401
        assert c.get(path, auth=("sc", PASSWORD)).status_code == 200


def test_index_shows_last_run_and_errors(tmp_path):
    add_run(tmp_path, 3, found=321)
    add_run(tmp_path, 1, ok=False, found=0, error="client_id nicht ermittelbar")
    r = client_for(tmp_path).get("/", auth=("sc", PASSWORD))
    assert r.headers["content-type"].startswith("text/html")
    assert "sc-digger" in r.text
    assert "client_id nicht ermittelbar" in r.text
    assert "321" in r.text


def test_index_escapes_html_in_error_text(tmp_path):
    add_run(tmp_path, 1, ok=False, error="<script>alert(1)</script>")
    r = client_for(tmp_path).get("/", auth=("sc", PASSWORD))
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_password_is_never_rendered(tmp_path):
    add_run(tmp_path, 1)
    c = client_for(tmp_path)
    assert PASSWORD not in c.get("/", auth=("sc", PASSWORD)).text
    assert PASSWORD not in c.get("/api/status", auth=("sc", PASSWORD)).text


def test_api_status_returns_json(tmp_path):
    add_run(tmp_path, 2, found=55)
    add_track(tmp_path, "a", "inbox")
    r = client_for(tmp_path).get("/api/status", auth=("sc", PASSWORD))
    assert r.headers["content-type"].startswith("application/json")
    data = r.json()
    assert data["last_discover"]["found"] == 55
    assert data["inbox_total"] == 1
    assert "healthy" in data and "runs" in data and "stats" in data


def test_no_api_docs_exposed(tmp_path):
    c = client_for(tmp_path)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path, auth=("sc", PASSWORD)).status_code == 404


def test_only_get_is_allowed(tmp_path):
    c = client_for(tmp_path)
    for path in ("/", "/api/status"):
        assert c.post(path, auth=("sc", PASSWORD)).status_code in (404, 405)


# ------------------------------------------------------------------ Startbefehl
def test_main_refuses_to_start_without_password(tmp_path, monkeypatch, capsys):
    import sc_digger.web.__main__ as entry
    monkeypatch.delenv("SC_DIGGER_WEB_PASSWORD", raising=False)

    def no_start(*a, **k):
        raise AssertionError("uvicorn darf nicht starten")

    monkeypatch.setattr("uvicorn.run", no_start)
    assert entry.main(["--config", str(ROOT / "config.yaml")]) == 2
    assert "SC_DIGGER_WEB_PASSWORD" in capsys.readouterr().err


def test_main_starts_uvicorn_with_safe_defaults(monkeypatch):
    import sc_digger.web.__main__ as entry
    monkeypatch.setenv("SC_DIGGER_WEB_PASSWORD", PASSWORD)
    started = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: started.update(app=app, **kw))
    assert entry.main(["--config", str(ROOT / "config.yaml")]) == 0
    assert started["host"] == "127.0.0.1" and started["port"] == 8080
    assert started["app"] is not None


def test_main_accepts_host_and_port(monkeypatch):
    import sc_digger.web.__main__ as entry
    monkeypatch.setenv("SC_DIGGER_WEB_PASSWORD", PASSWORD)
    started = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: started.update(**kw))
    entry.main(["--config", str(ROOT / "config.yaml"), "--host", "0.0.0.0", "--port", "9090"])
    assert started["host"] == "0.0.0.0" and started["port"] == 9090


# ------------------------------------------------------------------ Betrieb: standardmäßig aus
def test_entrypoint_starts_web_only_with_password_and_keeps_bot_last():
    text = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    assert "SC_DIGGER_WEB_PASSWORD" in text and "sc_digger.web" in text
    assert text.index("SC_DIGGER_WEB_PASSWORD") < text.index("sc_digger.web")
    assert text.index("sc_digger.web") < text.index("exec python -m sc_digger.bot")
    last_line = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")][-1]
    assert last_line.strip() == "exec python -m sc_digger.bot"


def test_requirements_list_web_dependencies():
    names = {ln.split("#")[0].strip().lower() for ln in
             (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()}
    for dep in ("fastapi", "uvicorn", "jinja2", "httpx"):
        assert any(n.startswith(dep) for n in names), dep
