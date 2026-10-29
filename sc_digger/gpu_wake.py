"""Workstation täglich wecken, wenn Cue-Jobs offen sind (Issue #409, Teil 3 von #227).

Läuft als Oneshot im Container (``deploy/sc-digger-gpu-wake.service``, Timer nach dem
Tageslauf). Gibt es offene Cue-Jobs in der Job-Queue, schickt es eine Anfrage an den
Wake-Proxy vor der Workstation; der Proxy weckt den PC und blockiert, bis er antwortet. Der
Runner dort (``sc-digger-gpu-worker.service``) arbeitet die Queue ab und beendet sich bei
Leerlauf, das Herunterfahren übernimmt das Idle-Skript der Workstation.

Die Adresse des Proxys steht nur in der Umgebung (``SC_DIGGER_WAKE_URL``), nie im Repo und nie
in Log oder Meldung.
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable

import requests

from .redact import install_redacting_logging

log = logging.getLogger("sc_digger.gpu_wake")

# Der Proxy blockiert beim Kaltstart bis der PC antwortet (35 bis 50 s); großzügig bemessen.
WAKE_TIMEOUT_S = 150.0


def count_pending_jobs(db_path: str | Path, job_type: str = "cues") -> int:
    """Zählt offene (``pending``) Jobs dieses Typs.

    Öffnet die DB nur lesend (``mode=ro``), damit ein falscher Pfad nie eine leere Datei
    anlegt. Fehlende Datei oder SQLite-Fehler ergeben 0; der Pfad steht nicht im Log.
    """
    path = Path(db_path)
    if not path.is_file():
        log.warning("Track-DB nicht gefunden, keine offenen Jobs gezählt")
        return 0
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'pending' AND job_type = ?",
                (job_type,),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        log.warning("Track-DB nicht lesbar (%s), keine offenen Jobs gezählt", type(exc).__name__)
        return 0
    return int(row[0]) if row else 0


def wake_workstation(wake_url: str, *, post: Callable[..., Any] | None = None,
                     timeout: float = WAKE_TIMEOUT_S) -> bool:
    """Schickt den Weck-Anruf. True bei Status < 500, sonst (und bei Netzfehlern) False.

    Auch 404 gilt als Erfolg: der Proxy hat den PC geweckt, nur das Modell der Anfrage ist
    unbekannt. Die URL wird nie geloggt, die Ausnahmen von ``requests`` enthalten sie.
    """
    post = post if post is not None else requests.post
    try:
        response = post(wake_url, json={"name": "wake"}, timeout=timeout)
    except requests.RequestException as exc:
        log.warning("Workstation wecken fehlgeschlagen (%s)", type(exc).__name__)
        return False
    if response.status_code >= 500:
        log.warning("Workstation wecken fehlgeschlagen (HTTP %s)", response.status_code)
        return False
    return True


def run_wake(db_path: str | Path, wake_url: str, *, post: Callable[..., Any] | None = None,
             notify: Callable[[str], None] | None = None) -> int:
    """Weckt die Workstation, wenn Cue-Jobs offen sind. Rückgabe: Exit-Code (0 oder 1)."""
    pending = count_pending_jobs(db_path)
    if pending == 0:
        log.info("Keine offenen Cue-Jobs, Workstation bleibt aus")
        return 0
    log.info("%d offene Cue-Job(s), wecke die Workstation", pending)
    if wake_workstation(wake_url, post=post):
        return 0
    if notify is not None:
        notify(f"⚠️ Workstation konnte nicht geweckt werden, {pending} Cue-Job(s) bleiben offen.")
    return 1


def _telegram_notifier() -> Callable[[str], None] | None:
    """Meldung per Telegram, nur über ``output.telegram_call`` (Regel 5); None ohne Zugang."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return None

    def notify(text: str) -> None:
        from .output import TelegramError, telegram_call

        try:
            telegram_call(token, "sendMessage", json={"chat_id": chat_id, "text": text})
        except TelegramError as exc:
            log.warning("Telegram-Meldung nicht gesendet: %s", exc)

    return notify


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sc_digger.gpu_wake",
        description="Weckt die GPU-Workstation, wenn Cue-Jobs offen sind.",
    )
    parser.add_argument("--config", default="config.yaml", help="Konfigurationsdatei")
    parser.add_argument(
        "--wake-url",
        default=os.environ.get("SC_DIGGER_WAKE_URL"),
        help="Adresse des Wake-Proxys (Standard: Umgebungsvariable SC_DIGGER_WAKE_URL)",
    )
    args = parser.parse_args(argv)
    install_redacting_logging(logging.INFO)

    if not args.wake_url:
        print(
            "Fehler: keine Wake-Adresse. --wake-url setzen oder SC_DIGGER_WAKE_URL füllen.",
            file=sys.stderr,
        )
        return 2

    from .models import Config

    cfg = Config.load(args.config)
    return run_wake(
        cfg["state"]["track_db_path"], args.wake_url, notify=_telegram_notifier()
    )


if __name__ == "__main__":
    raise SystemExit(main())
