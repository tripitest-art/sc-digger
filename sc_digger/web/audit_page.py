"""Lesezugriff auf den letzten Sammlungs-Audit-Bericht (Issue #657)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_last_report(report_path: str | Path) -> dict[str, Any] | None:
    """{"path": str(report_path), "written_at": <ISO aus mtime>, "size": <Bytes>} oder None,
    wenn die Datei fehlt oder nicht lesbar ist."""
    try:
        p = Path(report_path)
        if not p.is_file():
            return None
        stat = p.stat()
        written_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
        return {
            "path": str(p),
            "written_at": written_at,
            "size": stat.st_size,
        }
    except OSError:
        return None
