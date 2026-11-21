"""Wartungsgerüst des Dashboards (Issue #655): Aufträge im Hintergrund starten.

Ein Auftrag je Aktion (`scout`, `watch`, `taste`). Der Zustand liegt in einer gemeinsamen JSON-Datei
neben der State-DB, damit die Sperre einen Neustart des Containers überlebt und ein zweiter Klick
keinen zweiten Lauf derselben Aktion startet. Ein Eintrag je Aktion: ein laufender Scout
überschreibt so nie den Abschluss des Wächters.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..models import Config

log = logging.getLogger(__name__)

MESSAGE_MAX = 300

# Prozessweite Sperre für den Lese-Ändern-Schreiben-Zyklus der Datei (mehrere Threads, ein Prozess).
_LOCK = threading.Lock()


def _now_iso(now: datetime | None) -> str:
    if now is None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
    return now.isoformat(timespec="seconds")


def _read_all(state_path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_all(state_path: str | Path, data: dict[str, Any]) -> None:
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_task_state(state_path: str | Path, action: str) -> dict[str, Any] | None:
    """Zustand einer Aktion aus der gemeinsamen Datei oder None, wenn die Datei, das JSON-Objekt
    oder der Eintrag fehlt. Eintrag: {"action", "state", "started_at", "finished_at", "message"}."""
    entry = _read_all(state_path).get(action)
    return entry if isinstance(entry, dict) else None


def finish_task(state_path: str | Path, action: str, *, ok: bool, message: str,
                now: datetime | None = None) -> None:
    """Setzt state "done" bzw. "error", finished_at und message (auf 300 Zeichen gekürzt)."""
    with _LOCK:
        data = _read_all(state_path)
        entry = data.get(action)
        entry = dict(entry) if isinstance(entry, dict) else {"action": action, "started_at": None}
        entry["action"] = action
        entry["state"] = "done" if ok else "error"
        entry["finished_at"] = _now_iso(now)
        entry["message"] = str(message)[:MESSAGE_MAX]
        data[action] = entry
        _write_all(state_path, data)


def start_task(action: str, runner: Callable[[], str], *, state_path: str | Path,
               now: datetime | None = None) -> bool:
    """True, wenn gestartet wurde; False, wenn für **dieselbe** Aktion schon state == "running"
    in der Datei steht (Sperre, kein zweiter Thread). Andere Aktionen dürfen parallel laufen.
    Schreibt synchron den Eintrag action/state=running/started_at (ISO) in die Datei — die übrigen
    Einträge bleiben erhalten — und startet `runner` in einem daemon-Thread.
    Ende: finish_task(..., ok=True, message=<Rückgabe des runner>);
    Ausnahme: finish_task(..., ok=False, message="<Typname>: Lauf abgebrochen>") — nie der
    Ausnahmetext selbst (CodeQL py/stack-trace-exposure)."""
    with _LOCK:
        data = _read_all(state_path)
        current = data.get(action)
        if isinstance(current, dict) and current.get("state") == "running":
            return False
        data[action] = {
            "action": action,
            "state": "running",
            "started_at": _now_iso(now),
            "finished_at": None,
            "message": "",
        }
        _write_all(state_path, data)

    def _run() -> None:
        try:
            message = runner()
        except Exception as exc:  # noqa: BLE001 - jeder Fehler wird zum Zustand "error"
            log.warning("Wartung %s abgebrochen: %s", action, type(exc).__name__)
            finish_task(state_path, action, ok=False,
                        message=f"{type(exc).__name__}: Lauf abgebrochen")
            return
        finish_task(state_path, action, ok=True, message=str(message or ""))

    threading.Thread(target=_run, name=f"wartung-{action}", daemon=True).start()
    return True


def default_runners(cfg: Config) -> dict[str, Callable[[], str]]:
    """Aktionen dieser Ausbaustufe: {"scout": ..., "watch": ..., "taste": ...}.
    `run_scout` lokal aus `sc_digger.main` importieren (kein Import auf Modulebene, main.py zieht
    argparse), `run_watch` aus `sc_digger.watch`, Taste über `TrackDB` + `run_taste`.
    Rückgabe: kurze deutsche Ergebniszeile für die Statusanzeige."""

    def scout() -> str:
        from ..main import run_scout

        n = run_scout(cfg)
        return f"Scout fertig: {n} neue Kaufempfehlung(en)"

    def watch() -> str:
        from ..watch import run_watch

        code = run_watch(cfg)
        if code != 0:
            raise RuntimeError("Wächter meldet Fehler")
        return "Wächter fertig"

    def taste() -> str:
        from ..db import TrackDB
        from ..taste import run_taste

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with TrackDB(cfg["state"]["track_db_path"]) as db:
            result = run_taste(db, now=now)
        if result.trained:
            return f"Taste-Score trainiert, {result.scored} Tracks bewertet"
        return f"Taste-Score nicht trainiert: {result.reason}"

    return {"scout": scout, "watch": watch, "taste": taste}


def action_labels() -> dict[str, str]:
    """Deutsche Beschriftung je Aktion, z. B. {"scout": "Scout starten", ...}."""
    return {
        "scout": "Scout starten",
        "watch": "Wächter prüfen",
        "taste": "Taste-Score trainieren",
    }
