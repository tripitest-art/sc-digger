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
    def evaluate(self, mode: str, threshold: int, *, zero_is_bad: bool = True) -> str | None:
        """Gibt eine Alarm- oder Entwarnungsnachricht zurück, oder None.

        Pro Ausfall wird nur einmal alarmiert und einmal entwarnt.
        zero_is_bad=False: nur ok=0 gilt als schlecht. Für Cron-Jobs wie Sicherung oder Scout ist
        „0 gefunden“ normal und kein Ausfall.
        """
        runs = self._last_runs(mode, max(threshold, 1))
        if not runs:
            return None
        bad = [(not ok) or (zero_is_bad and found == 0) for _, ok, found, _ in runs]
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
                if "RateLimitError" in last_error:
                    lines.append("SoundCloud drosselt die Anfragen (429) "
                                 "→ weniger Tags/Referenz-Accounts oder kleineres limit_per_tag.")
                if "SOUNDCLOUD_AUTH_TOKEN" in last_error:
                    lines.append("SoundCloud-Login abgelaufen "
                                 "→ neuen Token mit <code>./set-secret.sh</code> setzen.")
                if "backup.target_dir" in last_error:
                    lines.append("Die Sicherung hat kein Ziel "
                                 "→ <code>backup.target_dir</code> setzen, siehe BETRIEB.md "
                                 "(Abschnitt Sicherung und Wiederherstellung).")
                if "Workstation" in last_error:
                    lines.append(
                        "Die Job-Queue wird nicht abgearbeitet → Timer <code>sc-digger-gpu-wake</code> "
                        "und Worker auf der Workstation prüfen (BETRIEB.md, Prüfliste: "
                        "Dienste außerhalb des Containers)."
                    )
                if "Speicher knapp" in last_error or "Inbox nicht prüfbar" in last_error:
                    lines.append(
                        "Downloads sind pausiert, bis wieder genug Platz frei ist "
                        "→ Inbox und <code>_rejected</code> aufräumen."
                    )
            else:
                lines.append("Keine Fehler, aber 0 Rohtreffer: Such-Endpunkt oder Filter "
                             "haben sich vermutlich geändert → <code>soundcloud.py</code> (search_tag) prüfen.")
            return "\n".join(lines)

        if active and not bad[0]:
            self._set_alert(mode, False)
            return (f"✅ <b>sc-digger: {mode} läuft wieder</b>\n"
                    f"Letzter Lauf: {runs[0][2]} Rohtreffer.")
        return None


def record_job(db_path: str | Path, mode: str, *, ok: bool, found: int = 0,
               error: str | None = None, threshold: int = 2) -> str | None:
    """Protokolliert den Lauf eines Cron-Jobs in runs und liefert Alarm- bzw. Entwarnungstext oder None.

    Wirft nie: Ein Fehler im Protokoll darf den eigentlichen Job nicht zusätzlich zu Fall bringen.
    """
    try:
        with Health(db_path) as health:
            health.record(mode, ok=ok, found=found, error=error)
            return health.evaluate(mode, threshold, zero_is_bad=False)
    except Exception as exc:  # noqa: BLE001 - bewusst breit, siehe Docstring
        log.warning("Lauf %s konnte nicht protokolliert werden: %s", mode, redact(str(exc)))
        return None


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
