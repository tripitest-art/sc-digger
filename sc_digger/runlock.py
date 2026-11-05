"""Lauf-Sperre: schreibende Läufe (discover, audit, scout, …) nie gleichzeitig.

Warum: Cron-Jobs (06:45 audit, 07:30 discover, 18:00 scout) und Läufe von Hand schreiben in
dieselbe Inbox und dieselben SQLite-Dateien. Ein langer Audit, der in den discover-Lauf
hineinreicht, führte sonst zu doppelten Downloads, „database is locked“ und halb geschriebener
Rekordbox-XML.

Die Sperre ist ein fcntl.flock auf <lock_dir>/pipeline.lock. flock gibt der Kernel beim
Prozessende selbst frei; „veraltete“ Sperren nach einem Absturz gibt es deshalb nicht, und die
Datei wird nie gelöscht (löschen und neu anlegen wäre ein Wettlauf zwischen zwei Prozessen).
In der Datei steht, wer die Sperre hält, damit eine Absage den Halter nennen kann.
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

try:  # Linux/macOS
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]
    import msvcrt

LOCK_NAME = "pipeline.lock"


def busy_text(holder: dict | None) -> str:
    """Klartext für Log und Health-Alarm, wenn die Sperre belegt ist."""
    if not holder:
        return "Lauf-Sperre belegt"
    return f"Lauf-Sperre belegt: {holder.get('mode', '?')} läuft seit {holder.get('started', '?')}"


class RunLockBusy(RuntimeError):
    """Die Sperre blieb bis zum Ende der Wartezeit belegt."""

    def __init__(self, holder: dict | None):
        super().__init__(busy_text(holder))
        self.holder = holder


def read_holder(lock_dir: str | Path) -> dict | None:
    """Inhalt der Sperrdatei (mode, pid, started) oder None. Wirft nie."""
    try:
        text = (Path(lock_dir) / LOCK_NAME).read_text(encoding="utf-8").strip()
        data = json.loads(text) if text else None
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data else None


def _try_lock(fd: int) -> bool:
    """Ein nicht blockierender Versuch; True bei Erfolg."""
    if fcntl is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True
    try:  # pragma: no cover - Windows
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return
    os.lseek(fd, 0, os.SEEK_SET)  # pragma: no cover - Windows
    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # pragma: no cover - Windows


def _write(fd: int, text: str) -> None:
    os.ftruncate(fd, 0)
    if text:
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, text.encode("utf-8"))


@contextmanager
def run_lock(lock_dir: str | Path, mode: str, *, wait_s: float = 0.0, poll_s: float = 5.0,
             sleep: Callable[[float], None] = time.sleep,
             clock: Callable[[], float] = time.monotonic) -> Iterator[Path]:
    """Exklusive Lauf-Sperre in lock_dir; wartet höchstens wait_s Sekunden in poll_s-Schritten.

    Belegt nach Ablauf der Wartezeit -> RunLockBusy (mit dem Halter aus der Sperrdatei).
    Jeder Aufruf öffnet die Datei selbst: So schließen sich auch zwei Sperren im selben
    Prozess aus (flock gilt pro offener Datei, nicht pro Prozess).
    """
    d = Path(lock_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / LOCK_NAME
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        start = clock()
        while not _try_lock(fd):
            if clock() - start >= wait_s:
                raise RunLockBusy(read_holder(d))
            sleep(poll_s)
        try:
            _write(fd, json.dumps({"mode": mode, "pid": os.getpid(),
                                   "started": datetime.now().isoformat(timespec="seconds")}))
            yield path
        finally:
            # Leeren statt löschen: Ein wartender Prozess hält die Datei womöglich schon offen.
            _write(fd, "")
            _unlock(fd)
    finally:
        os.close(fd)
