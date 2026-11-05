"""Speicherplatz-Wächter für die Inbox (Issue #459).

Prüft vor dem Download-Schritt den freien Speicherplatz unter `download.inbox_dir`.
Ist der Platz knapp (< min_free_gb), pausiert der Lauf Downloads und alarmiert einmalig
per Telegram-Healthcheck.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Callable

GB = 1024 ** 3


@dataclass(frozen=True)
class DiskStatus:
    path: str
    free_gb: float
    min_free_gb: float

    @property
    def ok(self) -> bool:
        """True, wenn genügend freier Speicher vorhanden ist."""
        return self.free_gb >= self.min_free_gb


def check_disk(
    path: str | Path,
    min_free_gb: float,
    *,
    usage: Callable = shutil.disk_usage,
) -> DiskStatus:
    """Prüft den freien Speicherplatz auf dem Dateisystem von `path`.

    Existiert `path` nicht, wird der nächste vorhandene Elternordner gemessen.
    Genau ein usage-Aufruf; usage liefert ein Objekt mit .free (Bytes).
    OSError wird durchgereicht.
    """
    target = Path(path)
    while not target.exists() and target != target.parent:
        target = target.parent

    res = usage(str(target))
    free_gb = round(res.free / GB, 1)
    return DiskStatus(
        path=str(path),
        free_gb=free_gb,
        min_free_gb=float(min_free_gb),
    )
