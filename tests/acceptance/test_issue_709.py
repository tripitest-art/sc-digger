"""Akzeptanztests: Alarm, wenn die Workstation offene Jobs tagelang nicht abarbeitet (Issue <N>)."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import yaml

import sc_digger.canary as canary
from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from sc_digger.queue_watch import MODE, check_queue, run_queue_watch
from sc_digger.soundcloud import SoundCloudError
from sc_digger.web.status import ALERT_LABELS

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 25, 7, 15, 0)


def _jobs(track_db: Path, rows: list[tuple[str, str]]) -> None:
    """rows: (status, created_at) je Job."""
    with TrackDB(track_db) as db:
        rec = db.upsert_track(TrackRecord(path=str(track_db.parent / "a.wav"), mtime=0.0, size=0,
                                          status=TrackStatus.INBOX))
        track_id = rec.id
    con = sqlite3.connect(str(track_db))
    try:
        for status, created in rows:
            con.execute("INSERT INTO jobs (track_id, job_type, status, created_at, updated_at) "
                        "VALUES (?, 'cues', ?, ?, ?)", (track_id, status, created, created))
        con.commit()
    finally:
        con.close()


def _runs(db: Path) -> list[tuple]:
    con = sqlite3.connect(str(db))
    try:
        return con.execute("SELECT ok, found, error FROM runs WHERE mode=? ORDER BY id", (MODE,)).fetchall()
    finally:
        con.close()


def test_ohne_datenbank_kein_stillstand(tmp_path):
    q = check_queue(tmp_path / "fehlt.sqlite", now=NOW)
    assert (q.pending, q.oldest_pending_at, q.age_days, q.stale) == (0, None, None, False)
    assert not (tmp_path / "fehlt.sqlite").exists()


def test_aeltester_offener_job_entscheidet(tmp_path):
    db = tmp_path / "tracks.sqlite"
    _jobs(db, [("pending", "2026-11-21 07:00:00"), ("pending", "2026-11-24 07:00:00"),
               ("completed", "2026-10-01 07:00:00")])
    q = check_queue(db, now=NOW)
    assert q.pending == 2
    assert q.oldest_pending_at == "2026-11-21 07:00:00"
    assert 4.0 < q.age_days < 4.1
    assert q.stale is True
    assert check_queue(db, now=NOW, stale_days=5).stale is False


def test_junge_jobs_sind_kein_stillstand(tmp_path):
    db = tmp_path / "tracks.sqlite"
    _jobs(db, [("pending", "2026-11-23 08:00:00")])
    assert check_queue(db, now=NOW).stale is False


def _cfg(tmp_path: Path) -> Config:
    return Config(raw={"state": {"db_path": str(tmp_path / "seen.sqlite"),
                                 "track_db_path": str(tmp_path / "tracks.sqlite")},
                       "gpu_wake": {"stale_alert_days": 3}})


def test_stillstand_alarmiert_einmal_und_entwarnt(tmp_path):
    _jobs(tmp_path / "tracks.sqlite", [("pending", "2026-11-20 07:00:00")])
    meldungen: list[str] = []

    assert run_queue_watch(_cfg(tmp_path), now=NOW, notify=meldungen.append) == 1
    assert run_queue_watch(_cfg(tmp_path), now=NOW, notify=meldungen.append) == 1
    assert len(meldungen) == 1 and "Workstation" in meldungen[0]

    con = sqlite3.connect(str(tmp_path / "tracks.sqlite"))
    con.execute("UPDATE jobs SET status = 'completed'")
    con.commit()
    con.close()
    assert run_queue_watch(_cfg(tmp_path), now=NOW, notify=meldungen.append) == 0
    assert len(meldungen) == 2

    runs = _runs(tmp_path / "seen.sqlite")
    assert [r[0] for r in runs] == [0, 0, 1]
    assert runs[0][1] == 1 and "seit 5 Tagen" in runs[0][2]


def test_leere_queue_ist_unauffaellig(tmp_path):
    _jobs(tmp_path / "tracks.sqlite", [])
    meldungen: list[str] = []
    assert run_queue_watch(_cfg(tmp_path), now=NOW, notify=meldungen.append) == 0
    assert meldungen == []
    assert _runs(tmp_path / "seen.sqlite") == [(1, 0, None)]


def test_ohne_state_pfade_passiert_nichts():
    assert run_queue_watch(Config(raw={}), now=NOW) == 0


def test_kanarienvogel_meldet_den_stillstand_auch_ohne_soundcloud(tmp_path, monkeypatch):
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)
    _jobs(tmp_path / "tracks.sqlite", [("pending", "2026-01-01 07:00:00")])
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({
        "search": {"tags": ["schranz"]},
        "state": {"db_path": str(tmp_path / "seen.sqlite"), "track_db_path": str(tmp_path / "tracks.sqlite")},
        "canary": {"tag": "", "track_url": ""},
    }), encoding="utf-8")

    class KaputtSC:
        def _get(self, *a, **k):
            raise SoundCloudError("nicht erreichbar")

    gesendet: list = []
    monkeypatch.setattr(canary, "SoundCloudClient", lambda *a, **k: KaputtSC())
    monkeypatch.setattr(canary, "send_telegram", lambda cfg, msgs, *a, **k: gesendet.append(msgs))

    assert canary.main(["--config", str(cfg_file)]) == 3
    assert len(gesendet) == 1 and "Workstation" in "\n".join(gesendet[0])
    assert _runs(tmp_path / "seen.sqlite")[0][0] == 0


def test_alarmname_und_standard():
    assert ALERT_LABELS[MODE] == "KI-Workstation"
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert raw["gpu_wake"]["stale_alert_days"] == 3
