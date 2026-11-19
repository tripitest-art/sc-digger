"""Modultests für sc_digger/web/workstation.py (ohne HTTP, Issue #628)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sc_digger.models import Config
from sc_digger.web import workstation
from sc_digger.web.workstation import (
    DEFAULT_COOLDOWN_S,
    WorkstationRun,
    read_state,
    start_run,
    wake_url_of,
)


def _cfg(tmp_path: Path, *, cooldown_s: int = 300, wake_url: str | None = None) -> Config:
    raw = {
        "state": {
            "db_path": str(tmp_path / "seen.sqlite"),
            "track_db_path": str(tmp_path / "tracks.sqlite"),
            "gpu_wake_state": str(tmp_path / "gpu_wake.json"),
        },
        "gpu_wake": {
            "cooldown_s": cooldown_s,
        },
    }
    if wake_url is not None:
        raw["gpu_wake"]["wake_url"] = wake_url
    return Config(raw=raw)


def test_read_state_fehlende_datei(tmp_path: Path):
    cfg = _cfg(tmp_path)
    state = read_state(cfg)
    assert state == WorkstationRun(started_at=None, status="unbekannt", pending=None, error=None)


def test_read_state_kaputte_datei_wirft_nicht(tmp_path: Path):
    cfg = _cfg(tmp_path)
    state_file = tmp_path / "gpu_wake.json"

    # Kaputtes JSON
    state_file.write_text("{kaputtes json...", encoding="utf-8")
    assert read_state(cfg) == WorkstationRun(None, "unbekannt", None, None)

    # Kein Dict im JSON
    state_file.write_text("[\"liste\", \"statt\", \"dict\"]", encoding="utf-8")
    assert read_state(cfg) == WorkstationRun(None, "unbekannt", None, None)

    # Leere Datei
    state_file.write_text("", encoding="utf-8")
    assert read_state(cfg) == WorkstationRun(None, "unbekannt", None, None)


def test_read_state_liest_gueltige_datei_und_redigiert_fehler(tmp_path: Path):
    cfg = _cfg(tmp_path)
    state_file = tmp_path / "gpu_wake.json"
    data = {
        "started_at": "2026-11-19T10:00:00+00:00",
        "status": "fehler",
        "pending": 5,
        "error": "Fehler mit geheimem Token: secret_token_abc123",
    }
    state_file.write_text(json.dumps(data), encoding="utf-8")
    state = read_state(cfg)
    assert state.started_at == "2026-11-19T10:00:00+00:00"
    assert state.status == "fehler"
    assert state.pending == 5
    assert state.error is not None
    assert state.to_dict()["status"] == "fehler"


def test_wake_url_of_bevorzugt_umgebungsvariable(monkeypatch, tmp_path: Path):
    cfg = _cfg(tmp_path, wake_url="http://config.internal/run")

    # Weder Env noch Config -> None
    cfg_empty = _cfg(tmp_path)
    monkeypatch.delenv("SC_DIGGER_WAKE_URL", raising=False)
    assert wake_url_of(cfg_empty) is None

    # Nur Config gesetzt
    assert wake_url_of(cfg) == "http://config.internal/run"

    # Umgebungsvariable gesetzt: gewinnt über die Config
    monkeypatch.setenv("SC_DIGGER_WAKE_URL", "http://env.internal/run")
    assert wake_url_of(cfg) == "http://env.internal/run"


def test_keine_jobs_schreibt_keinen_startzeitpunkt(monkeypatch, tmp_path: Path):
    cfg = _cfg(tmp_path, wake_url="http://wake.internal/run")
    wake_calls: list[str] = []
    monkeypatch.setattr(workstation, "count_pending_jobs", lambda *_a, **_k: 0)
    monkeypatch.setattr(workstation, "wake_workstation", lambda url, **_k: wake_calls.append(url) or True)

    status_code, body = start_run(cfg)
    assert status_code == 200
    assert body == {"status": "keine-jobs", "pending": 0, "started_at": None}
    assert wake_calls == []

    # Zustandsdatei prüfen: started_at muss None sein
    saved = read_state(cfg)
    assert saved.status == "keine-jobs"
    assert saved.pending == 0
    assert saved.started_at is None
    assert saved.error is None


def test_cooldown_greift_und_erlaubt_start_nach_ablauf(monkeypatch, tmp_path: Path):
    cfg = _cfg(tmp_path, cooldown_s=DEFAULT_COOLDOWN_S, wake_url="http://wake.internal/run")
    wake_calls: list[str] = []
    count_calls: list[int] = []

    def mock_count(*_a, **_k):
        count_calls.append(1)
        return 3

    monkeypatch.setattr(workstation, "count_pending_jobs", mock_count)
    monkeypatch.setattr(workstation, "wake_workstation", lambda url, **_k: wake_calls.append(url) or True)

    t0 = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
    status_code, body = start_run(cfg, now=t0)
    assert status_code == 202
    assert body["status"] == "gestartet"
    assert body["started_at"] == t0.isoformat()
    assert len(wake_calls) == 1
    assert len(count_calls) == 1

    # Zweiter Klick 100 Sekunden später (innerhalb 300 s Cooldown): 409, kein Zählen, kein Wecken
    t1 = t0 + timedelta(seconds=100)
    status_code_2, body_2 = start_run(cfg, now=t1)
    assert status_code_2 == 409
    assert body_2["error"] == "lauf aktiv"
    assert body_2["started_at"] == t0.isoformat()
    assert len(wake_calls) == 1
    assert len(count_calls) == 1

    # Dritter Klick 301 Sekunden später (Cooldown abgelaufen): Wecken erneut erlaubt
    t2 = t0 + timedelta(seconds=301)
    status_code_3, body_3 = start_run(cfg, now=t2)
    assert status_code_3 == 202
    assert body_3["status"] == "gestartet"
    assert body_3["started_at"] == t2.isoformat()
    assert len(wake_calls) == 2
    assert len(count_calls) == 2
