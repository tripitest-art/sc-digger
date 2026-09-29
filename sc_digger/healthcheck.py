"""Container-Healthcheck: Cron, letzten Lauf und DB-Integrität prüfen."""
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import telegram

from sc_digger.db import Health
from sc_digger.output import telegram_call

# Zugangsdaten aus Umgebungsvariablen (siehe entrypoint.sh, cron.env)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


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
    try:
        # Nur numerische PID-Ordner prüfen
        for pid in proc_root.iterdir():
            if pid.is_dir() and pid.name.isdigit():
                comm_path = pid / "comm"
                if comm_path.exists():
                    try:
                        comm_text = comm_path.read_text(encoding="utf-8").strip()
                        if comm_text == "cron":
                            return CheckResult(name="cron", ok=True, detail="ok")
                    except (OSError, UnicodeDecodeError):
                        # unreadable comm-Datei wird übersprungen
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
        conn = sqlite3.connect(str(db_path), uri=True, timeout=5.0)
        conn.execute("PRAGMA query_only=ON")  # nur lesen
        cursor = conn.execute(
            "SELECT finished_at, mode FROM runs WHERE mode='discover' ORDER BY finished_at DESC LIMIT 1"
        )
        row = cursor.fetchone()
        conn.close()

        if row is None:
            return CheckResult(name="letzter_lauf", ok=True, detail="noch kein Lauf")

        finished_at_str = row[0]
        finished_at = datetime.fromisoformat(finished_at_str.replace("Z", "+00:00"))
        age_hours = (now - finished_at).total_seconds() / 3600

        if age_hours <= max_age_hours:
            return CheckResult(name="letzter_lauf", ok=True, detail="ok")
        else:
            return CheckResult(name="letzter_lauf", ok=False, detail=f"letzter Lauf vor {age_hours:.1f} Stunden")
    except sqlite3.Error:
        return CheckResult(name="letzter_lauf", ok=False, detail="DB nicht lesbar")


def check_sqlite(db_path: str | Path, name: str) -> CheckResult:
    """Prüft die Integrität einer SQLite-DB.

    PRAGMA quick_check == "ok". Datei fehlt -> ok ("noch nicht angelegt"), nichts anlegen.
    sqlite3.Error (z.B. "file is not a database") -> nicht ok, wirft nie.
    Nur lesend öffnen.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return CheckResult(name=name, ok=True, detail="noch nicht angelegt")

    try:
        conn = sqlite3.connect(str(db_path), uri=True, timeout=5.0)
        conn.execute("PRAGMA query_only=ON")
        cursor = conn.execute("PRAGMA quick_check")
        result = cursor.fetchone()[0]
        conn.close()

        if result == "ok":
            return CheckResult(name=name, ok=True, detail="ok")
        else:
            return CheckResult(name=name, ok=False, detail="DB beschädigt")
    except sqlite3.Error as e:
        return CheckResult(name=name, ok=False, detail=f"DB-Fehler: {e}")


def run_checks(cfg: dict, *, proc_root: Path = Path("/proc"),
               now: datetime | None = None) -> List[CheckResult]:
    """Führt alle Health-Prüfungen aus.

    cron, letzter_lauf (cfg["state"]["db_path"], health.max_hours_since_run, Standard 36),
    state_db (cfg["state"]["db_path"]), track_db (cfg["state"]["track_db_path"]).
    """
    results = []

    # cron prüfen
    results.append(check_cron(proc_root=proc_root))

    # letzter_lauf prüfen
    state_db_path = cfg.get("state", {}).get("db_path")
    if state_db_path:
        health_cfg = cfg.get("health", {})
        max_hours = health_cfg.get("max_hours_since_run", 36)
        results.append(check_last_run(state_db_path, max_hours, now=now))

    # state_db Integrität prüfen
    results.append(check_sqlite(state_db_path, "state_db"))

    # track_db Integrität prüfen
    track_db_path = cfg.get("state", {}).get("track_db_path")
    if track_db_path:
        results.append(check_sqlite(track_db_path, "track_db"))

    return results


def alert_message(state_file: Path, results: List[CheckResult]) -> str | None:
    """Erstellt Alarm- oder Entwarnungsnachricht.

    Alarm-Zustand in einer JSON-Datei (nicht in der SQLite-DB, die könnte ja kaputt sein).
    Wechsel gesund -> krank: Alarmtext mit einer Zeile je fehlgeschlagener Prüfung
    ("<name>: <detail>"); Wechsel krank -> gesund: Entwarnung, die das Wort "wieder" enthält;
    sonst None. Speichert den neuen Zustand nur bei einem Wechsel.
    """
    state_file = state_file or Path("/data/container_health.json")

    # Neuen Zustand serialisieren
    new_state = json.dumps([{"name": r.name, "ok": r.ok, "detail": r.detail} for r in results],
                          ensure_ascii=False, indent=2)

    # Lese alten Zustand
    old_state_json = None
    if state_file.exists():
        try:
            old_state_json = state_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            old_state_json = None

    # Wenn sich der Zustand geändert hat
    if old_state_json is None or new_state_json != old_state_json:
        # Neue Status speichern
        try:
            state_file.write_text(new_state_json, encoding="utf-8")
        except (OSError, PermissionError):
            pass  # kann nicht speichern, aber Meldung trotzdem senden

        # Prüfen, ob sich der Gesamtzustand geändert hat
        all_ok_now = all(r.ok for r in results)
        all_ok_before = old_state_json is None or all(json.loads(old_state_json)[i]["ok"]
                                                    for i in range(len(results)))

        if all_ok_now and not all_ok_before:
            # Wieder gesund -> Entwarnung
            failed_checks = [json.loads(old_state_json)[i] for i in range(len(results))
                            if not json.loads(old_state_json)[i]["ok"]]
            if failed_checks:
                recovery_msg = "Container wieder gesund!\n"
                for fc in failed_checks:
                    recovery_msg += f"- {fc['name']}: {fc['detail']}\n"
                return recovery_msg.strip()
        elif not all_ok_now and all_ok_before:
            # Wurde krank -> Alarm
            failed_checks = [r for r in results if not r.ok]
            alarm_msg = "Container nicht gesund!\n"
            for fc in failed_checks:
                alarm_msg += f"- {fc['name']}: {fc['detail']}\n"
            return alarm_msg.strip()

    return None


def main(argv: list[str] | None = None, *, proc_root: Path = Path("/proc")) -> int:
    """Hauptfunktion für den HEALTHCHECK.

    --config (Standard config.yaml). Führt run_checks aus, druckt eine Zeile je Prüfung,
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

    # Konfiguration laden
    try:
        import yaml
        cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    except (FileNotFoundError, yaml.YAMLError) as e:
        print(f"Konfigurationsdatei nicht lesbar: {e}")
        return 1

    # Prüfungen ausführen
    now = datetime.now(timezone.utc)
    results = run_checks(cfg, proc_root=proc_root, now=now)

    # Ergebnisse drucken
    for result in results:
        status = "ok" if result.ok else "FEHLER"
        print(f"{result.name}: {status} - {result.detail}")

    # Meldungsdatei
    state_dir = Path(cfg["state"]["db_path"]).parent
    state_file = state_dir / "container_health.json"

    # Alarm-/Entwarnungsnachricht erstellen
    message = alert_message(state_file, results)

    # Telegram senden, wenn es eine Meldung gibt und Tokens gesetzt sind
    if message and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            telegram_call(TELEGRAM_BOT_TOKEN, "sendMessage",
                         chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode="Markdown")
        except telegram.error.TelegramError as e:
            # Telegram-Fehler wird geloggt, ändert Exit-Code nicht
            print(f"Telegram-Fehler: {e}")

    # Exit-Code
    return 0 if all(r.ok for r in results) else 1
