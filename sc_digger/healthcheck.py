"""Container-Healthcheck: Cron, letzten Lauf und DB-Integrität prüfen."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sc_digger.models import Config
from sc_digger.output import TelegramError, telegram_call


@dataclass
class CheckResult:
    """Ergebnis einer einzelnen Prüfung."""
    name: str  # "cron" | "letzter_lauf" | "state_db" | "track_db"
    ok: bool
    detail: str  # kurze Begründung auf Deutsch, ohne Zugangsdaten


def check_cron(proc_root: Path = Path("/proc")) -> CheckResult:
    """Prüft, ob ein Prozess namens 'cron' läuft.

    ok, wenn ein Prozess mit /proc/<pid>/comm == "cron" existiert. Nur numerische
    Unterordner zählen. Liest /proc direkt (python:3.12-slim hat kein ps/pgrep).
    """
    proc_root = Path(proc_root)
    try:
        if not proc_root.exists():
            return CheckResult(name="cron", ok=False, detail="cron läuft nicht")
        for pid_entry in proc_root.iterdir():
            if pid_entry.is_dir() and pid_entry.name.isdigit():
                comm_path = pid_entry / "comm"
                if not comm_path.exists():
                    continue
                try:
                    comm_text = comm_path.read_text(encoding="utf-8").strip()
                    if comm_text == "cron":
                        return CheckResult(name="cron", ok=True, detail="ok")
                except (OSError, UnicodeDecodeError):
                    continue
        return CheckResult(name="cron", ok=False, detail="cron läuft nicht")
    except (OSError, PermissionError):
        return CheckResult(name="cron", ok=False, detail="cron läuft nicht")


def check_last_run(db_path: str | Path, max_age_hours: float,
                   now: datetime | None = None) -> CheckResult:
    """Prüft, wann der letzte 'discover'-Lauf stattfand.

    Jüngster runs-Eintrag mit mode='discover' (egal ob ok oder nicht; ein gescheiterter Lauf
    beweist, dass Cron lief). finished_at ist UTC (SQLite CURRENT_TIMESTAMP). Älter als
    max_age_hours -> nicht ok.
    DB-Datei fehlt / Tabelle fehlt / leer -> ok ("noch kein Lauf").
    Nur lesend öffnen (sqlite3 "file:...?mode=ro", uri=True); legt nie Dateien an.
    now: timezone-aware UTC, Standard datetime.now(timezone.utc).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    db_path = Path(db_path)
    if not db_path.exists():
        return CheckResult(name="letzter_lauf", ok=True, detail="noch kein Lauf")

    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5.0)
        try:
            cursor = conn.execute(
                "SELECT finished_at FROM runs WHERE mode='discover' ORDER BY finished_at DESC LIMIT 1"
            )
            row = cursor.fetchone()
        except sqlite3.OperationalError as e:
            if "no such table" in str(e).lower():
                return CheckResult(name="letzter_lauf", ok=True, detail="noch kein Lauf")
            raise
        finally:
            conn.close()

        if row is None:
            return CheckResult(name="letzter_lauf", ok=True, detail="noch kein Lauf")

        finished_at_str = row[0]
        finished_at = datetime.fromisoformat(finished_at_str.replace("Z", "+00:00"))
        if finished_at.tzinfo is None:
            finished_at = finished_at.replace(tzinfo=timezone.utc)

        age_hours = (now - finished_at).total_seconds() / 3600

        if age_hours <= max_age_hours:
            return CheckResult(name="letzter_lauf", ok=True, detail="ok")
        else:
            return CheckResult(name="letzter_lauf", ok=False, detail=f"letzter Lauf vor {age_hours:.1f} Stunden")
    except sqlite3.Error:
        return CheckResult(name="letzter_lauf", ok=False, detail="DB nicht lesbar")


def check_sqlite(db_path: str | Path, name: str) -> CheckResult:
    """PRAGMA quick_check == "ok". Datei fehlt -> ok ("noch nicht angelegt"), nichts anlegen.
    sqlite3.Error (z. B. "file is not a database") -> nicht ok, wirft nie. Nur lesend öffnen.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return CheckResult(name=name, ok=True, detail="noch nicht angelegt")

    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5.0)
        try:
            cursor = conn.execute("PRAGMA quick_check")
            row = cursor.fetchone()
            result = row[0] if row else "unknown"
        finally:
            conn.close()

        if result == "ok":
            return CheckResult(name=name, ok=True, detail="ok")
        else:
            return CheckResult(name=name, ok=False, detail="DB beschädigt")
    except sqlite3.Error as e:
        return CheckResult(name=name, ok=False, detail=f"DB-Fehler: {e}")


def run_checks(cfg: Config, *, proc_root: Path = Path("/proc"),
               now: datetime | None = None) -> list[CheckResult]:
    """cron, letzter_lauf (cfg["state"]["db_path"], health.max_hours_since_run, Standard 36),
    state_db (cfg["state"]["db_path"]), track_db (cfg["state"]["track_db_path"])."""
    results: list[CheckResult] = []

    # 1. cron
    results.append(check_cron(proc_root=proc_root))

    raw = cfg.raw if hasattr(cfg, "raw") else cfg
    state = raw.get("state", {})
    health = raw.get("health", {})
    state_db_path = state.get("db_path")
    track_db_path = state.get("track_db_path")
    max_hours = float(health.get("max_hours_since_run", 36))

    # 2. letzter_lauf
    if state_db_path:
        results.append(check_last_run(state_db_path, max_hours, now=now))

    # 3. state_db
    if state_db_path:
        results.append(check_sqlite(state_db_path, "state_db"))

    # 4. track_db
    if track_db_path:
        results.append(check_sqlite(track_db_path, "track_db"))

    return results


def alert_message(state_file: Path, results: list[CheckResult]) -> str | None:
    """Alarm-Zustand in einer JSON-Datei (nicht in der SQLite-DB, die könnte ja kaputt sein).
    Wechsel gesund -> krank: Alarmtext mit einer Zeile je fehlgeschlagener Prüfung
    ("<name>: <detail>"); Wechsel krank -> gesund: Entwarnung, die das Wort "wieder" enthält;
    sonst None. Speichert den neuen Zustand nur bei einem Wechsel.
    """
    state_file = Path(state_file) if state_file else Path("/data/container_health.json")
    all_ok = all(r.ok for r in results)
    curr_alert = not all_ok

    prev_alert = False
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                prev_alert = bool(data.get("alert", False))
            elif isinstance(data, list):
                prev_alert = any(not item.get("ok", True) for item in data if isinstance(item, dict))
        except (OSError, ValueError, TypeError):
            prev_alert = False

    if curr_alert == prev_alert:
        return None

    # Neuer Zustand speichern nur bei Wechsel
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"alert": curr_alert}), encoding="utf-8")
    except OSError:
        pass

    if curr_alert:
        # Wechsel gesund -> krank
        lines = [f"{r.name}: {r.detail}" for r in results if not r.ok]
        return "\n".join(lines)
    else:
        # Wechsel krank -> gesund: Entwarnung, die das Wort "wieder" enthält
        return "Container ist wieder gesund."


def main(argv: list[str] | None = None, *, proc_root: Path = Path("/proc")) -> int:
    """--config (Standard config.yaml). Führt run_checks aus, druckt eine Zeile je Prüfung,
    state_file = Path(cfg["state"]["db_path"]).parent / "container_health.json".
    Gibt es eine Meldung und sind TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID gesetzt: senden über
    telegram_call(token, "sendMessage", ...) (Name im Modul importiert, damit Tests es ersetzen
    können). TelegramError wird geloggt und ändert den Exit-Code nicht.
    Exit-Code 0, wenn alle Prüfungen ok sind, sonst 1.
    """
    import argparse

    parser = argparse.ArgumentParser(description="sc-digger Container-Healthcheck")
    parser.add_argument("--config", type=str, default="config.yaml",
                        help="Pfad zur Konfigurationsdatei (Standard: config.yaml)")
    args = parser.parse_args(argv)

    try:
        cfg = Config.load(args.config)
    except Exception as e:
        print(f"Konfigurationsdatei nicht lesbar: {e}")
        return 1

    results = run_checks(cfg, proc_root=proc_root)

    for result in results:
        status = "ok" if result.ok else "FEHLER"
        print(f"{result.name}: {status} - {result.detail}")

    raw = cfg.raw if hasattr(cfg, "raw") else cfg
    state_db_path = raw.get("state", {}).get("db_path", "/data/seen.sqlite")
    state_file = Path(state_db_path).parent / "container_health.json"

    message = alert_message(state_file, results)

    token = cfg.telegram_token
    chat_id = cfg.telegram_chat_id
    if message and token and chat_id:
        try:
            telegram_call(token, "sendMessage", json={"chat_id": chat_id, "text": message})
        except TelegramError as e:
            print(f"Telegram-Fehler: {e}")

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
