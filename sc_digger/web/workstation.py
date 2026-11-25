"""Logik für den manuellen Start eines KI-Workstation-Laufs aus dem Web-Dashboard (Issue #628).

Zustandsdatei lesen/schreiben, Sperrzeit (Cooldown) überwachen und den Wake-Proxy anfragen,
wenn Cue- oder Embedding-Jobs in der Job-Queue warten. Die Wake-Adresse steht nie im Repo und erscheint nie
in Antworten, Logmeldungen oder Fehlern.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..gpu_wake import count_pending_jobs, wake_workstation
from ..models import Config
from ..redact import redact

log = logging.getLogger("sc_digger.web.workstation")

DEFAULT_COOLDOWN_S = 300


@dataclass(frozen=True)
class WorkstationRun:
    started_at: str | None      # ISO-8601 UTC des letzten Starts, None = nie gestartet
    status: str                 # "unbekannt" | "gestartet" | "keine-jobs" | "fehler"
    pending: int | None         # gezählte offene Jobs (Cues, Embeddings) beim letzten Klick
    error: str | None           # Klartext, redigiert; None ohne Fehler

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_state(cfg: Config, *, now: datetime | None = None) -> WorkstationRun:
    """Liest cfg["state"]["gpu_wake_state"]; fehlende oder kaputte Datei -> WorkstationRun(None, "unbekannt", None, None).

    Legt nichts an, wirft nie.
    """
    try:
        raw_state = cfg["state"] if hasattr(cfg, "__getitem__") else getattr(cfg, "raw", {}).get("state", {})
        if not isinstance(raw_state, dict):
            return WorkstationRun(None, "unbekannt", None, None)
        path_str = raw_state.get("gpu_wake_state")
        if not path_str:
            return WorkstationRun(None, "unbekannt", None, None)
        path = Path(path_str)
        if not path.is_file():
            return WorkstationRun(None, "unbekannt", None, None)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return WorkstationRun(None, "unbekannt", None, None)
        err = data.get("error")
        return WorkstationRun(
            started_at=data.get("started_at"),
            status=str(data.get("status") or "unbekannt"),
            pending=data.get("pending"),
            error=redact(err) if err else None,
        )
    except Exception:
        return WorkstationRun(None, "unbekannt", None, None)


def _write_state(cfg: Config, run: WorkstationRun) -> None:
    """Schreibt den Zustand atomar in die konfigurierte Zustandsdatei."""
    try:
        raw_state = cfg["state"] if hasattr(cfg, "__getitem__") else getattr(cfg, "raw", {}).get("state", {})
        if not isinstance(raw_state, dict):
            return
        path_str = raw_state.get("gpu_wake_state")
        if not path_str:
            return
        path = Path(path_str)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(run.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        log.warning("Zustandsdatei konnte nicht geschrieben werden: %s", exc)


def wake_url_of(cfg: Config) -> str | None:
    """cfg["gpu_wake"].get("wake_url") oder die Umgebungsvariable SC_DIGGER_WAKE_URL (diese gewinnt), sonst None."""
    env = os.environ.get("SC_DIGGER_WAKE_URL")
    if env:
        return env
    try:
        raw_gpu = cfg["gpu_wake"] if hasattr(cfg, "__getitem__") else getattr(cfg, "raw", {}).get("gpu_wake", {})
        if isinstance(raw_gpu, dict):
            url = raw_gpu.get("wake_url")
            if url:
                return str(url)
    except Exception:
        pass
    return None


def _is_cooling_down(state: WorkstationRun, cooldown_s: int, now: datetime) -> bool:
    """Prüft, ob der letzte erfolgreiche Start jünger als die Sperrzeit ist."""
    if state.status != "gestartet" or not state.started_at:
        return False
    if cooldown_s <= 0:
        return False
    try:
        started_dt = datetime.fromisoformat(state.started_at.replace("Z", "+00:00"))
        if started_dt.tzinfo is None:
            started_dt = started_dt.replace(tzinfo=timezone.utc)
        now_utc = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        elapsed = (now_utc - started_dt).total_seconds()
        return elapsed < cooldown_s
    except Exception:
        return False


def start_run(cfg: Config, *, post: Callable[..., Any] | None = None,
              now: datetime | None = None) -> tuple[int, dict]:
    """(HTTP-Status, JSON-Body) für POST /api/gpu-wake.

    Schreibt die Zustandsdatei nur bei Erfolg, bei Fehlschlag des Weckrufs und bei „keine Jobs".
    """
    now_utc = now if now is not None else datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    # 1. Wake-Adresse prüfen: ohne Zieladresse wird kein Weckversuch unternommen.
    wake_url = wake_url_of(cfg)
    if not wake_url:
        return 502, {"error": "kein Wake-Proxy konfiguriert"}

    # 2. Sperrzeit (Cooldown): schützt vor Doppelstarts während des Kaltstarts der Workstation.
    state = read_state(cfg, now=now_utc)
    cooldown_s = DEFAULT_COOLDOWN_S
    try:
        raw_gpu = cfg["gpu_wake"] if hasattr(cfg, "__getitem__") else getattr(cfg, "raw", {}).get("gpu_wake", {})
        if isinstance(raw_gpu, dict) and "cooldown_s" in raw_gpu:
            cooldown_s = int(raw_gpu["cooldown_s"])
    except (ValueError, TypeError, KeyError):
        cooldown_s = DEFAULT_COOLDOWN_S

    if _is_cooling_down(state, cooldown_s, now_utc):
        return 409, {"error": "lauf aktiv", "started_at": state.started_at}

    # 3. Job-Queue prüfen: sind keine offenen Jobs (Cues, Embeddings) da, bleibt die Workstation aus.
    track_db_path = cfg["state"]["track_db_path"]
    pending = count_pending_jobs(track_db_path, None)
    if pending == 0:
        new_state = WorkstationRun(started_at=None, status="keine-jobs", pending=0, error=None)
        _write_state(cfg, new_state)
        return 200, {"status": "keine-jobs", "pending": 0, "started_at": None}

    # 4. Weckruf an den Proxy senden.
    started_iso = now_utc.isoformat()
    ok = wake_workstation(wake_url, post=post)
    if ok:
        new_state = WorkstationRun(started_at=started_iso, status="gestartet", pending=pending, error=None)
        _write_state(cfg, new_state)
        return 202, {"status": "gestartet", "pending": pending, "started_at": started_iso}
    else:
        err_msg = "Workstation konnte nicht geweckt werden"
        new_state = WorkstationRun(started_at=started_iso, status="fehler", pending=pending, error=err_msg)
        _write_state(cfg, new_state)
        return 502, {"error": err_msg, "pending": pending}
