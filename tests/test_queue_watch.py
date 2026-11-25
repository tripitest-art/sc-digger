"""Eigene Tests zu sc_digger/queue_watch.py (Issue #709)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sc_digger.db import TrackDB
from sc_digger.health import Health
from sc_digger.models import Config
from sc_digger.queue_watch import MODE, check_queue, run_queue_watch
from sc_digger.schema import FIELDS

NOW = datetime(2026, 11, 25, 7, 15, 0)


def _db_mit_job(path: Path, created: str, status: str = "pending") -> None:
    TrackDB(path).close()
    con = sqlite3.connect(str(path))
    con.execute("INSERT INTO tracks (path, mtime, size, status) VALUES ('a', 0, 0, 'inbox')")
    con.execute("INSERT INTO jobs (track_id, job_type, status, created_at, updated_at) "
                "VALUES (1, 'cues', ?, ?, ?)", (status, created, created))
    con.commit()
    con.close()


def test_tzaware_now_wird_nach_utc_umgerechnet(tmp_path):
    db = tmp_path / "t.sqlite"
    _db_mit_job(db, "2026-11-22 07:15:00")
    q = check_queue(db, now=datetime(2026, 11, 25, 8, 15, tzinfo=timezone.utc))
    assert q.stale and 3.0 < q.age_days < 3.1


def test_genau_an_der_schwelle_ist_stillstand(tmp_path):
    db = tmp_path / "t.sqlite"
    _db_mit_job(db, "2026-11-22 07:15:00")
    assert check_queue(db, now=NOW).stale is True


def test_tabelle_fehlt_wirft_nicht(tmp_path):
    db = tmp_path / "leer.sqlite"
    sqlite3.connect(str(db)).close()
    assert check_queue(db, now=NOW).pending == 0


def test_ungueltige_zeit_wirft_nicht(tmp_path):
    db = tmp_path / "t.sqlite"
    _db_mit_job(db, "kaputt")
    assert check_queue(db, now=NOW).stale is False


def test_schwelle_aus_config(tmp_path):
    _db_mit_job(tmp_path / "tracks.sqlite", "2026-11-23 07:15:00")
    cfg = Config(raw={"state": {"db_path": str(tmp_path / "seen.sqlite"),
                                "track_db_path": str(tmp_path / "tracks.sqlite")},
                      "gpu_wake": {"stale_alert_days": 1}})
    assert run_queue_watch(cfg, now=NOW) == 1


def test_fehler_im_notify_wird_nur_geloggt(tmp_path):
    _db_mit_job(tmp_path / "tracks.sqlite", "2026-11-01 07:15:00")
    cfg = Config(raw={"state": {"db_path": str(tmp_path / "seen.sqlite"),
                                "track_db_path": str(tmp_path / "tracks.sqlite")}})

    def kaputt(text: str) -> None:
        raise RuntimeError("telegram weg")

    assert run_queue_watch(cfg, now=NOW, notify=kaputt) == 1


def test_alarmtext_enthaelt_hinweis(tmp_path):
    with Health(tmp_path / "h.sqlite") as h:
        h.record(MODE, ok=False, found=3, error="3 Job(s) warten seit 5 Tagen, die Workstation arbeitet sie nicht ab")
        text = h.evaluate(MODE, 1, zero_is_bad=False)
    assert "sc-digger-gpu-wake" in text and "BETRIEB.md" in text


def test_schema_feld_vorhanden():
    f = next(x for x in FIELDS if x.path == "gpu_wake.stale_alert_days")
    assert (f.min, f.max) == (1, 60)
