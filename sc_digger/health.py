"""Health-Monitoring: Laufprotokoll und Alarm, wenn die Discovery wiederholt leer bleibt oder scheitert.

Die inoffizielle SoundCloud-API bricht gelegentlich (client_id-Wechsel, Endpunkt-Änderungen).
Ohne Monitoring sieht das aus wie ein ruhiger Tag. Deshalb:

- Jeder Lauf wird mit roher Trefferzahl (vor Filtern) und Fehlerstatus protokolliert.
- Sind die letzten N Läufe schlecht (Fehler oder 0 Rohtreffer), gibt es EINEN Alarm.
- Sobald ein Lauf wieder Treffer liefert, gibt es EINE Entwarnung.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from .redact import redact

log = logging.getLogger(__name__)


class Health:
    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path))
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS runs ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " mode TEXT NOT NULL,"
            " finished_at TEXT DEFAULT CURRENT_TIMESTAMP,"
            " ok INTEGER NOT NULL,"
            " found INTEGER NOT NULL,"
            " error TEXT);"
            "CREATE TABLE IF NOT EXISTS health_state (key TEXT PRIMARY KEY, value TEXT);"
        )
        self.db.commit()

    def __enter__(self) -> "Health":
        return self

    def __exit__(self, *exc: object) -> None:
        self.db.close()

    # ------------------------------------------------------------ Protokoll
    def record(self, mode: str, *, ok: bool, found: int, error: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO runs (mode, ok, found, error) VALUES (?,?,?,?)",
            (mode, int(ok), found, redact(error) if error else None),
        )
        self.db.commit()

    def _last_runs(self, mode: str, n: int) -> list[tuple]:
        return self.db.execute(
            "SELECT finished_at, ok, found, error FROM runs WHERE mode=? ORDER BY id DESC LIMIT ?",
            (mode, n),
        ).fetchall()

    def _alert_active(self, mode: str) -> bool:
        row = self.db.execute(
            "SELECT value FROM health_state WHERE key=?", (f"alert:{mode}",)
        ).fetchone()
        return bool(row and row[0] == "1")

    def _set_alert(self, mode: str, active: bool) -> None:
        self.db.execute(
            "INSERT INTO health_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (f"alert:{mode}", "1" if active else "0"),
        )
        self.db.commit()

    # ------------------------------------------------------------ Bewertung
    def evaluate(self, mode: str, threshold: int) -> str | None:
        """Gibt eine Alarm- oder Entwarnungsnachricht zurück, oder None.

        Pro Ausfall wird nur einmal alarmiert und einmal entwarnt.
        """
        runs = self._last_runs(mode, max(threshold, 1))
        if not runs:
            return None
        bad = [(not ok) or found == 0 for _, ok, found, _ in runs]
        active = self._alert_active(mode)

        if not active and len(runs) >= threshold and all(bad):
            self._set_alert(mode, True)
            since = runs[-1][0]
            last_error = next((err for _, _, _, err in runs if err), None)
            lines = [
                f"🚨 <b>sc-digger: {mode} liefert seit {len(runs)} Läufen nichts</b>",
                f"Seit: {since} UTC",
            ]
            if last_error:
                lines.append(f"Letzter Fehler: <code>{_esc(last_error[:300])}</code>")
                if "client_id" in last_error:
                    lines.append("Vermutlich hat SoundCloud die client_id-Auslieferung geändert "
                                 "→ <code>soundcloud.py</code> (_fetch_client_id) prüfen.")
            else:
                lines.append("Keine Fehler, aber 0 Rohtreffer: Such-Endpunkt oder Filter "
                             "haben sich vermutlich geändert → <code>soundcloud.py</code> (search_tag) prüfen.")
            return "\n".join(lines)

        if active and not bad[0]:
            self._set_alert(mode, False)
            return (f"✅ <b>sc-digger: {mode} läuft wieder</b>\n"
                    f"Letzter Lauf: {runs[0][2]} Rohtreffer.")
        return None


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
