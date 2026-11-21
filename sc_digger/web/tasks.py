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
    """Enthält zusätzlich: "backfill-energy", "backfill-artist", "backfill-embeddings",
    "requeue", "backup". Jeder Runner: TrackDB über cfg["state"]["track_db_path"] öffnen und
    schließen, Aufruf mit dry_run=False, Rückgabe einer deutschen Zeile mit den Zahlen aus dem
    Ergebnis (z. B. "Energie: 12 gesetzt, 3 übersprungen"), maximal 300 Zeichen."""

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

    def backfill_energy_runner() -> str:
        from ..backfill import backfill_energy
        from ..db import TrackDB

        with TrackDB(cfg["state"]["track_db_path"]) as db:
            res = backfill_energy(db, dry_run=False)
        return f"Energie: {res.get('set', 0)} gesetzt, {res.get('skipped', 0)} übersprungen"[:MESSAGE_MAX]

    def backfill_artist_runner() -> str:
        from ..backfill_artist import backfill_artists
        from ..db import TrackDB

        state_path = Path(cfg["state"]["db_path"])
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup_path = state_path.with_name(f"{state_path.name}.bak-{ts}")
        counter = 1
        while backup_path.exists():
            backup_path = state_path.with_name(f"{state_path.name}.bak-{ts}-{counter}")
            counter += 1

        with TrackDB(cfg["state"]["track_db_path"]) as db:
            res = backfill_artists(
                cfg["state"]["db_path"],
                cfg["state"]["track_db_path"],
                apply=True,
                backup_path=backup_path,
            )
        seen_up = getattr(res, "seen_updated", None)
        if seen_up is None and isinstance(res, dict):
            seen_up = res.get("seen_updated", 0)
            rej_up = res.get("rejected_updated", 0)
            unmatched = res.get("unmatched", 0)
        else:
            seen_up = getattr(res, "seen_updated", 0)
            rej_up = getattr(res, "rejected_updated", 0)
            unmatched = getattr(res, "unmatched", 0)
        return (
            f"Künstler: {seen_up} seen, {rej_up} rejected aktualisiert, "
            f"{unmatched} ohne Treffer"
        )[:MESSAGE_MAX]

    def backfill_embeddings_runner() -> str:
        from ..db import TrackDB
        from ..embeddings import backfill_embedding_jobs

        with TrackDB(cfg["state"]["track_db_path"]) as db:
            summary = backfill_embedding_jobs(db, dry_run=False)
        queued = getattr(summary, "queued", None)
        if queued is None and isinstance(summary, dict):
            queued = summary.get("queued", 0)
            already = summary.get("already_queued", 0)
        else:
            queued = getattr(summary, "queued", 0)
            already = getattr(summary, "already_queued", 0)
        return f"Embeddings: {queued} eingereiht, {already} bereits aktiv"[:MESSAGE_MAX]

    def requeue_runner() -> str:
        from ..db import TrackDB
        from ..main import run_requeue_cmd

        with TrackDB(cfg["state"]["track_db_path"]):
            return run_requeue_cmd(cfg)[:MESSAGE_MAX]

    def backup_runner() -> str:
        from ..backup import run_backup
        from ..db import TrackDB

        with TrackDB(cfg["state"]["track_db_path"]):
            today = datetime.now(timezone.utc).date()
            code = run_backup(cfg, today=today)
        if code != 0:
            raise RuntimeError(f"Sicherung fehlgeschlagen (Code {code})")
        return f"Sicherung erfolgreich erstellt ({today.isoformat()})"[:MESSAGE_MAX]

    def audit() -> str:
        from ..audit import run_audit

        report_file = Path(cfg["state"]["db_path"]).parent / "audit-report.html"
        summary = run_audit(cfg, report_path=report_file, check_adopted=True, no_telegram=True)
        return (
            f"Audit fertig: {summary.scanned} gescannt, {summary.ok} ok, "
            f"{len(summary.adopted_tracks)} übernommen"
        )

    return {
        "scout": scout,
        "watch": watch,
        "taste": taste,
        "backfill-energy": backfill_energy_runner,
        "backfill-artist": backfill_artist_runner,
        "backfill-embeddings": backfill_embeddings_runner,
        "requeue": requeue_runner,
        "backup": backup_runner,
        "audit": audit,
    }


def action_labels() -> dict[str, str]:
    """Deutsche Beschriftung je Aktion, z. B. {"scout": "Scout starten", ...}."""
    return {
        "scout": "Scout starten",
        "watch": "Wächter prüfen",
        "taste": "Taste-Score trainieren",
        "backfill-energy": "Energie nachberechnen",
        "backfill-artist": "Künstler nachpflegen",
        "backfill-embeddings": "Audio-Embeddings einreihen",
        "requeue": "Hängende Jobs zurücksetzen",
        "backup": "Sicherung erstellen",
        "audit": "Sammlungs-Audit starten",
    }
