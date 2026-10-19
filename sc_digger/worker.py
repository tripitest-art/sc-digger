"""GPU-Worker-Client für die Job-Queue der Statusseite (Issue #332).

Der Runner läuft auf der Workstation und spricht ausschließlich HTTP mit dem Dashboard:
Job holen (``/api/jobs/claim``), Lebenszeichen senden (``/api/jobs/{id}/heartbeat``) und
Ergebnis melden (``/api/jobs/{id}/complete``). Dadurch braucht die Workstation weder die
Track-DB noch die Sammlung einzubinden; Zugangsdaten bleiben in der Umgebung.

Die eigentliche Rechenarbeit steckt nicht hier: Job-Handler (z. B. Embedding-Inferenz auf
der GPU) werden über ``handler_map`` eingehängt.
"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import time
from typing import Any, Callable

import requests
from requests.auth import HTTPBasicAuth

from .redact import install_redacting_logging

log = logging.getLogger("sc_digger.worker")

JobHandler = Callable[[dict[str, Any]], Any]

# Abstand der Lebenszeichen während eines laufenden Jobs. Der Server setzt hängende
# "running"-Jobs erst nach 30 bis 120 Minuten zurück (db.STALE_TIMEOUT_MINUTES); 30 s
# halten auch lange Embedding-Läufe sicher am Leben, ohne die API zu fluten.
HEARTBEAT_INTERVAL_SECONDS = 30.0

_FINISH_STATUSES = ("completed", "failed")


def _default_worker_id() -> str:
    """Kennung aus Hostname und PID – nur für Anmeldung und Logzeilen, kein Geheimnis."""
    return f"{socket.gethostname()}-{os.getpid()}"


class JobWorkerClient:
    """HTTP-Client für die Job-Queue-REST-API."""

    def __init__(
        self,
        api_url: str,
        password: str,
        worker_id: str | None = None,
        timeout: float = 10.0,
    ):
        if not api_url:
            raise ValueError("api_url ist leer")
        self.api_url = api_url.rstrip("/")
        self.password = password
        self.worker_id = worker_id or _default_worker_id()
        self.timeout = timeout
        # Eine Session für alle Aufrufe: spart Verbindungsaufbau beim Pollen.
        self._session = requests.Session()
        self._session.auth = HTTPBasicAuth(self.worker_id, self.password)

    def _post(self, path: str, payload: dict[str, Any] | None = None) -> requests.Response:
        return self._session.post(
            f"{self.api_url}{path}", json=payload or {}, timeout=self.timeout
        )

    def get_job_audio(self, job_id: int) -> bytes:
        """Lädt die Audiodatei des Jobs über ``GET /api/jobs/{job_id}/audio``.

        Gibt die Rohdaten zurück. HTTP- und Verbindungsfehler werden durchgereicht;
        eine abgelehnte Anmeldung (401) wird wie beim Claim als RuntimeError gemeldet.
        """
        response = self._session.get(
            f"{self.api_url}/api/jobs/{int(job_id)}/audio", timeout=self.timeout
        )
        if response.status_code == 401:
            raise RuntimeError("Job-API hat die Anmeldung abgelehnt (401)")
        response.raise_for_status()
        return response.content

    def claim_job(self, job_type: str | None = None) -> dict | None:
        """Beansprucht den nächsten Job. Gibt Job-Dict oder None zurück.

        Verbindungsfehler und HTTP-Fehler werden durchgereicht; eine abgelehnte Anmeldung
        (401) wird als RuntimeError gemeldet, damit der Lauf nicht still leer pollt.
        """
        payload: dict[str, Any] = {}
        if job_type:
            payload["job_type"] = job_type
        response = self._post("/api/jobs/claim", payload)
        if response.status_code == 401:
            raise RuntimeError("Job-API hat die Anmeldung abgelehnt (401)")
        response.raise_for_status()
        data = response.json()
        job = data.get("job") if isinstance(data, dict) else None
        return job if isinstance(job, dict) else None

    def heartbeat(self, job_id: int) -> bool:
        """Meldet Lebenszeichen für einen laufenden Job. True bei 200, sonst False.

        Wirft nie: ein verlorenes Lebenszeichen soll den Job nicht abbrechen, das
        Ergebnis wird trotzdem gemeldet.
        """
        try:
            response = self._post(f"/api/jobs/{int(job_id)}/heartbeat")
            return response.status_code == 200
        except Exception as exc:  # noqa: BLE001 – Netzfehler dürfen den Lauf nicht beenden
            log.debug("Heartbeat für Job %s fehlgeschlagen: %s", job_id, exc)
            return False

    def complete_job(
        self,
        job_id: int,
        status: str,
        result: dict | None = None,
        error: str | None = None,
    ) -> bool:
        """Meldet das Ergebnis (``completed`` oder ``failed``). True bei 200, sonst False.

        Wirft nie bei Netzfehlern; ein ungültiger Status ist ein Programmierfehler und
        wird als ValueError gemeldet.
        """
        if status not in _FINISH_STATUSES:
            raise ValueError(f"status muss eine von {_FINISH_STATUSES} sein, nicht {status!r}")
        try:
            response = self._post(
                f"/api/jobs/{int(job_id)}/complete",
                {"status": status, "result": result, "error": error},
            )
            return response.status_code == 200
        except Exception as exc:  # noqa: BLE001 – Netzfehler dürfen den Lauf nicht beenden
            log.warning("Ergebnis für Job %s konnte nicht gemeldet werden: %s", job_id, exc)
            return False


def execute_job(job: dict, handler_map: dict[str, Any] | None = None) -> dict:
    """Führt einen Job anhand seines ``job_type`` aus.

    Rückgabe: ``{"status": "completed", "result": ...}`` oder
    ``{"status": "failed", "error": ...}``. Fehler des Handlers werden gefangen und als
    ``failed`` zurückgegeben – der Worker soll an einem kaputten Track nicht sterben.
    """
    job_type = str(job.get("job_type") or "")
    payload = job.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    handler = (handler_map or {}).get(job_type)
    if handler is None:
        return {"status": "failed", "error": f"Kein Handler für Job-Typ {job_type!r}"}

    try:
        return {"status": "completed", "result": handler(payload)}
    except Exception as exc:  # noqa: BLE001 – Job-Fehler gehört ins Ergebnis, nicht in den Absturz
        log.warning("Job %s (%s) fehlgeschlagen: %s", job.get("id"), job_type, exc)
        return {"status": "failed", "error": str(exc) or exc.__class__.__name__}


def _heartbeat_until_stopped(client: JobWorkerClient, job_id: int, stop: threading.Event) -> None:
    """Sendet Lebenszeichen, bis ``stop`` gesetzt wird; erster Schlag nach einem Intervall."""
    while not stop.wait(HEARTBEAT_INTERVAL_SECONDS):
        if not client.heartbeat(job_id):
            log.debug("Heartbeat für Job %s wurde nicht bestätigt", job_id)


def _sleep(seconds: float, stop_event: Any | None) -> None:
    """Pause, die ein ``threading.Event`` zum sofortigen Abbruch nutzen kann."""
    if seconds <= 0:
        return
    if stop_event is not None:
        stop_event.wait(seconds)
    else:
        time.sleep(seconds)


def run_worker_loop(
    client: JobWorkerClient,
    job_type: str | None = None,
    poll_interval: float = 5.0,
    max_jobs: int | None = None,
    handler_map: dict[str, Any] | None = None,
    stop_event: Any | None = None,
) -> int:
    """Hauptschleife: Jobs holen, ausführen, Status melden.

    Ohne Job wird ``poll_interval`` gewartet (Backoff bei leerer Queue und bei
    Netzfehlern). ``max_jobs`` beendet die Schleife nach so vielen gemeldeten Jobs
    (auch ``failed`` zählt – sonst liefe ``--once`` bei einem kaputten Job endlos).
    ``stop_event`` ist ein ``threading.Event``-kompatibles Objekt für sauberes Stoppen.
    Ohne ``handler_map`` kommt das Standard-Mapping aus ``cue_infer.build_handler_map`` mit
    dem Cue-Handler zum Einsatz.
    Rückgabe: Anzahl der Jobs mit gemeldetem Endstatus.
    """
    handled = 0
    interval = max(0.0, float(poll_interval))

    if handler_map is None:
        from .cue_infer import build_handler_map

        handler_map = build_handler_map(client)

    while True:
        if stop_event is not None and stop_event.is_set():
            break
        if max_jobs is not None and handled >= max_jobs:
            break

        try:
            job = client.claim_job(job_type=job_type)
        except Exception as exc:  # noqa: BLE001 – Server kurz weg: warten, nicht sterben
            log.warning("Job-Abruf fehlgeschlagen: %s", exc)
            _sleep(interval, stop_event)
            continue

        if not job:
            _sleep(interval, stop_event)
            continue

        job_id = job.get("id")
        if job_id is None:
            log.warning("Job ohne id übersprungen: %s", job)
            _sleep(interval, stop_event)
            continue

        # Erstes Lebenszeichen sofort: der Job steht nach dem Claim auf "running", und
        # der Server setzt ihn sonst nach seinem Timeout wieder auf "pending".
        client.heartbeat(job_id)
        stop_heartbeat = threading.Event()
        heartbeat_thread = threading.Thread(
            target=_heartbeat_until_stopped,
            args=(client, job_id, stop_heartbeat),
            name=f"heartbeat-{job_id}",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            outcome = execute_job(job, handler_map)
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=1.0)

        if client.complete_job(
            job_id,
            outcome["status"],
            result=outcome.get("result"),
            error=outcome.get("error"),
        ):
            handled += 1
            log.info("Job %s (%s): %s", job_id, job.get("job_type"), outcome["status"])
        else:
            # Nicht gezählt: der Job bleibt serverseitig "running" und wird nach dem
            # Timeout erneut freigegeben. Ein neuer Claim würde ihn nur doppelt starten.
            log.warning(
                "Job %s: Ergebnis konnte nicht gemeldet werden, wird beim nächsten Lauf "
                "erneut beansprucht",
                job_id,
            )
            _sleep(interval, stop_event)

    return handled


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sc_digger.worker",
        description="GPU-Worker für die Job-Queue der sc-digger-Statusseite.",
    )
    parser.add_argument("--api-url", required=True, help="Basis-URL des Dashboards")
    parser.add_argument(
        "--password",
        default=os.environ.get("SC_DIGGER_WEB_PASSWORD"),
        help="Dashboard-Passwort (Standard: Umgebungsvariable SC_DIGGER_WEB_PASSWORD)",
    )
    parser.add_argument("--worker-id", default=None, help="Kennung dieses Runners im Log")
    parser.add_argument("--job-type", default=None, help="Nur Jobs dieses Typs beanspruchen")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Pause bei leerer Queue")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Genau einen Job bearbeiten und beenden",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP-Timeout in Sekunden")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug-Logging")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI-Einstieg: pollt die Job-API, bis ``--once`` erfüllt ist oder Strg+C kommt."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    install_redacting_logging(logging.DEBUG if args.verbose else logging.INFO)

    if not args.password:
        print(
            "Fehler: kein Passwort. --password setzen oder SC_DIGGER_WEB_PASSWORD füllen.",
            file=sys.stderr,
        )
        return 2

    client = JobWorkerClient(
        args.api_url, password=args.password, worker_id=args.worker_id, timeout=args.timeout
    )

    # Cue-Jobs brauchen die Audiodatei per HTTP; der Handler wird mit diesem Client gebunden.
    from .cue_infer import build_handler_map

    stop_event = threading.Event()
    try:
        processed = run_worker_loop(
            client,
            job_type=args.job_type,
            poll_interval=args.poll_interval,
            max_jobs=1 if args.once else None,
            handler_map=build_handler_map(client),
            stop_event=stop_event,
        )
    except KeyboardInterrupt:
        stop_event.set()
        log.info("Abbruch durch Strg+C")
        return 130

    log.info("%d Job(s) bearbeitet", processed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
