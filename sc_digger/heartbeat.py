"""Lebenszeichen langlebiger Prozesse (Bot, Web-Dashboard) für den Container-Healthcheck.

Jeder Prozess schreibt regelmäßig eine kleine JSON-Datei `heartbeat-<name>.json` neben die
State-DB. Der Healthcheck (`healthcheck.check_heartbeat`) liest Alter und Status. So fällt auf,
wenn ein Prozess gestorben ist (Datei veraltet) oder untätig ist, obwohl er arbeiten sollte
(Status `fehler`, z. B. Telegram lehnt den Bot-Token ab und der Bot schläft für immer).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .redact import redact

log = logging.getLogger(__name__)

STATUS_OK = "ok"        # Prozess läuft und arbeitet
STATUS_AUS = "aus"      # bewusst untätig, z. B. kein Telegram-Token konfiguriert (kein Fehler)
STATUS_FEHLER = "fehler"  # untätig, obwohl er arbeiten sollte (z. B. Token abgelehnt)

MAX_DETAIL = 200


@dataclass
class Lebenszeichen:
    ts: datetime      # timezone-aware, UTC
    status: str
    detail: str


def heartbeat_file(state_dir: str | Path, name: str) -> Path:
    """state_dir / f"heartbeat-{name}.json"."""
    return Path(state_dir) / f"heartbeat-{name}.json"


def write_heartbeat(state_dir: str | Path | None, name: str, *, status: str = STATUS_OK,
                    detail: str = "", now: datetime | None = None) -> bool:
    """Schreibt das Lebenszeichen atomar (Temp-Datei im selben Ordner + os.replace), damit der
    Healthcheck nie eine halb geschriebene Datei liest. Legt den Ordner nicht an: Fehlt er, ist
    die Konfiguration falsch, und das soll nicht durch einen neuen Ordner verdeckt werden.
    Wirft nie; True bei Erfolg."""
    if state_dir is None:
        return False
    tmp_name: str | None = None
    try:
        folder = Path(state_dir)
        if not folder.is_dir():
            return False
        if now is None:
            now = datetime.now(timezone.utc)
        # detail kann aus Fehlertexten stammen; Zugangsdaten dürfen nicht in die Datei.
        clean = redact(detail or "")[:MAX_DETAIL]
        payload = json.dumps({"ts": now.astimezone(timezone.utc).isoformat(),
                              "status": status, "detail": clean}, ensure_ascii=False)
        fd, tmp_name = tempfile.mkstemp(prefix=f".heartbeat-{name}.", suffix=".tmp", dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, heartbeat_file(folder, name))
        tmp_name = None
        return True
    except Exception as e:  # noqa: BLE001 - ein Lebenszeichen darf den Prozess nie stören
        log.debug("Lebenszeichen %s nicht geschrieben: %s", name, e)
        return False
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def read_heartbeat(state_dir: str | Path | None, name: str) -> Lebenszeichen | None:
    """None, wenn state_dir None ist, die Datei fehlt, kein gültiges JSON-Objekt enthält oder ts
    fehlt/nicht lesbar ist. Fehlt status, gilt STATUS_OK; fehlt detail, "". Wirft nie."""
    if state_dir is None:
        return None
    try:
        data = json.loads(heartbeat_file(state_dir, name).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        ts_raw = data.get("ts")
        if not isinstance(ts_raw, str):
            return None
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        status = data.get("status")
        detail = data.get("detail")
        return Lebenszeichen(ts=ts.astimezone(timezone.utc),
                             status=status if isinstance(status, str) and status else STATUS_OK,
                             detail=detail if isinstance(detail, str) else "")
    except Exception:  # noqa: BLE001 - kaputte oder fehlende Datei = kein Lebenszeichen
        return None


class HeartbeatThread:
    """Erneuert das Lebenszeichen im Hintergrund, auch während der Prozess blockiert schläft
    (z. B. `bot._idle_forever`)."""

    def __init__(self, state_dir: str | Path | None, name: str, *, interval_s: float = 60.0) -> None:
        self.state_dir = state_dir
        self.name = name
        self.interval_s = interval_s
        self._status = STATUS_OK
        self._detail = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _write(self) -> None:
        with self._lock:
            if self._stop.is_set():
                return
            write_heartbeat(self.state_dir, self.name, status=self._status, detail=self._detail)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            self._write()

    def start(self) -> None:
        """Schreibt sofort ein Lebenszeichen und startet den Daemon-Thread."""
        self._write()
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name=f"heartbeat-{self.name}",
                                            daemon=True)
            self._thread.start()

    def set_status(self, status: str, detail: str = "") -> None:
        """Setzt Status und Detail und schreibt sofort (ohne auf den nächsten Takt zu warten)."""
        with self._lock:
            self._status = status
            self._detail = detail
        self._write()

    def stop(self) -> None:
        """Beendet den Thread; schreibt nichts mehr."""
        with self._lock:
            self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
