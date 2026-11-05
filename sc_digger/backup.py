"""Tägliche Sicherung der SQLite-Datenbanken (konsistent, geprüft, rotiert).

Sichert die zentralen SQLite-Datenbanken (seen.sqlite, tracks.sqlite) und die lokale
Override-Datei (config.local.yaml) konsistent über die SQLite Backup-API.
Prüft jede Kopie per PRAGMA integrity_check und rotiert alte Bestände
(7 tägliche, 4 wöchentliche Stände).
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Callable

from .models import Config
from .redact import install_redacting_logging

log = logging.getLogger(__name__)


class BackupError(RuntimeError):
    """Fehler bei der Sicherung oder Wiederherstellung einer SQLite-Datenbank."""


def _cleanup(path: Path) -> None:
    path.unlink(missing_ok=True)
    path.with_name(f"{path.name}-wal").unlink(missing_ok=True)
    path.with_name(f"{path.name}-shm").unlink(missing_ok=True)


def backup_sqlite(src: str | Path, dest: str | Path) -> Path:
    """Sichert eine SQLite-Datenbank konsistent über die Backup-API.

    dest existiert -> FileExistsError. Elternordner von dest anlegen.
    src nur lesend öffnen (URI mode=ro), Connection.backup() nach dest,
    danach im Ziel PRAGMA journal_mode=DELETE (in sich geschlossene Datei, kein -wal daneben).
    sqlite3.Error -> dest entfernen, BackupError.
    Kopie mit PRAGMA integrity_check prüfen; Ergebnis != "ok" -> dest entfernen, BackupError.
    Rückgabe dest.
    """
    src_p = Path(src)
    dest_p = Path(dest)

    if dest_p.exists():
        raise FileExistsError(f"Ziel {dest_p} existiert bereits")
    if not src_p.is_file():
        raise BackupError(f"Quelldatei nicht gefunden: {src_p}")

    dest_p.parent.mkdir(parents=True, exist_ok=True)

    src_conn: sqlite3.Connection | None = None
    dest_conn: sqlite3.Connection | None = None
    try:
        src_uri = f"{src_p.resolve().as_uri()}?mode=ro"
        src_conn = sqlite3.connect(src_uri, uri=True)
        dest_conn = sqlite3.connect(dest_p)
        src_conn.backup(dest_conn)
        dest_conn.execute("PRAGMA journal_mode=DELETE")
    except sqlite3.Error as exc:
        if dest_conn is not None:
            dest_conn.close()
            dest_conn = None
        if src_conn is not None:
            src_conn.close()
            src_conn = None
        _cleanup(dest_p)
        raise BackupError(f"Sicherung von {src_p} nach {dest_p} fehlgeschlagen: {exc}") from exc
    finally:
        if dest_conn is not None:
            dest_conn.close()
        if src_conn is not None:
            src_conn.close()

    check_conn: sqlite3.Connection | None = None
    try:
        check_conn = sqlite3.connect(dest_p)
        rows = check_conn.execute("PRAGMA integrity_check").fetchall()
        if len(rows) != 1 or rows[0][0].lower() != "ok":
            raise BackupError(f"Integritätsprüfung für {dest_p} fehlgeschlagen: {rows}")
    except (sqlite3.Error, BackupError) as exc:
        if check_conn is not None:
            check_conn.close()
            check_conn = None
        _cleanup(dest_p)
        if isinstance(exc, BackupError):
            raise
        raise BackupError(f"Integritätsprüfung für {dest_p} fehlgeschlagen: {exc}") from exc
    finally:
        if check_conn is not None:
            check_conn.close()

    return dest_p


def backup_all(sources: list[Path], target_dir: str | Path, *, stamp: str) -> list[Path]:
    """Sichert alle übergebenen Quellen in einen Tagesordner unter target_dir/<stamp>/.

    Ziel: <target_dir>/<stamp>/<dateiname>. Existiert <target_dir>/<stamp> schon -> [] (nichts tun).
    Schreibt zuerst in <target_dir>/.<stamp>.tmp, am Ende per rename nach <stamp>.
    Fehler -> tmp-Ordner entfernen, Fehler weiterreichen.
    .sqlite-Dateien über backup_sqlite, alle anderen per shutil.copy2.
    Fehlende Quelle -> log.warning, überspringen.
    Rückgabe: Pfade im Tagesordner in der Reihenfolge von sources.
    """
    target = Path(target_dir)
    day_dir = target / stamp
    if day_dir.exists():
        log.info("Sicherungsordner %s existiert bereits, überspringe.", day_dir)
        return []

    target.mkdir(parents=True, exist_ok=True)
    tmp_dir = target / f".{stamp}.tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    backed_up: list[Path] = []
    try:
        for src in sources:
            src_p = Path(src)
            if not src_p.exists():
                log.warning("Quelldatei %s existiert nicht, überspringe.", src_p)
                continue
            dest_tmp = tmp_dir / src_p.name
            if src_p.suffix == ".sqlite":
                backup_sqlite(src_p, dest_tmp)
            else:
                shutil.copy2(src_p, dest_tmp)
            backed_up.append(day_dir / src_p.name)
        tmp_dir.rename(day_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    return backed_up


def prune_backups(target_dir: str | Path, *, keep_daily: int = 7, keep_weekly: int = 4) -> list[Path]:
    """Rotiert Sicherungsordner: behält keep_daily neueste und keep_weekly wöchentliche Stände.

    Betrachtet nur Unterordner, deren Name ein gültiges ISO-Datum ist (date.fromisoformat);
    alles andere bleibt unberührt.
    Behalten: die keep_daily neuesten; aus den übrigen je ISO-Kalenderwoche den neuesten,
    davon die keep_weekly neuesten Wochen.
    Alles andere per shutil.rmtree löschen.
    Rückgabe: gelöschte Ordner, sortiert. Fehlender target_dir -> [].
    """
    target = Path(target_dir)
    if not target.is_dir():
        return []

    date_dirs: dict[date, Path] = {}
    for entry in target.iterdir():
        if entry.is_dir():
            try:
                d = date.fromisoformat(entry.name)
                date_dirs[d] = entry
            except ValueError:
                continue

    if not date_dirs:
        return []

    sorted_dates = sorted(date_dirs.keys())
    daily_kept = set(sorted_dates[-keep_daily:] if keep_daily > 0 else [])
    remaining = [d for d in sorted_dates if d not in daily_kept]

    by_week: dict[tuple[int, int], list[date]] = defaultdict(list)
    for d in remaining:
        iso = d.isocalendar()
        by_week[(iso[0], iso[1])].append(d)

    weekly_candidates: list[date] = []
    for wk in sorted(by_week.keys(), reverse=True):
        weekly_candidates.append(max(by_week[wk]))

    weekly_kept = set(weekly_candidates[:keep_weekly] if keep_weekly > 0 else [])
    to_keep = daily_kept | weekly_kept

    deleted: list[Path] = []
    for d, folder in date_dirs.items():
        if d not in to_keep:
            shutil.rmtree(folder, ignore_errors=True)
            deleted.append(folder)

    deleted.sort()
    return deleted


def restore_sqlite(backup_file: str | Path, target: str | Path, *, force: bool = False) -> Path:
    """Spielt eine gesicherte SQLite-Datenbank zurück.

    Sicherung zuerst prüfen (integrity_check); kaputt -> BackupError, target unverändert.
    target existiert und force False -> FileExistsError, nichts geändert.
    force True -> bisheriges target nach "<name>.vor-restore" verschieben (überschreibt ältere),
    "<target>-wal" und "<target>-shm" löschen, dann per Backup-API nach target schreiben.
    Rückgabe target.
    """
    backup_path = Path(backup_file)
    target_path = Path(target)

    if not backup_path.is_file():
        raise BackupError(f"Sicherungsdatei nicht gefunden: {backup_path}")

    # Sicherung zuerst prüfen (integrity_check); kaputt -> BackupError, target unverändert.
    check_conn: sqlite3.Connection | None = None
    try:
        chk_uri = f"{backup_path.resolve().as_uri()}?mode=ro"
        check_conn = sqlite3.connect(chk_uri, uri=True)
        rows = check_conn.execute("PRAGMA integrity_check").fetchall()
        if len(rows) != 1 or rows[0][0].lower() != "ok":
            raise BackupError(f"Integritätsprüfung für {backup_path} fehlgeschlagen: {rows}")
    except (sqlite3.Error, BackupError) as exc:
        if check_conn is not None:
            check_conn.close()
            check_conn = None
        if isinstance(exc, BackupError):
            raise
        raise BackupError(f"Sicherung {backup_path} ist beschädigt: {exc}") from exc
    finally:
        if check_conn is not None:
            check_conn.close()

    if target_path.exists():
        if not force:
            raise FileExistsError(f"Zieldatei existiert bereits: {target_path}")
        vor_restore = target_path.with_name(f"{target_path.name}.vor-restore")
        vor_restore.unlink(missing_ok=True)
        target_path.rename(vor_restore)
        target_path.with_name(f"{target_path.name}-wal").unlink(missing_ok=True)
        target_path.with_name(f"{target_path.name}-shm").unlink(missing_ok=True)

    target_path.parent.mkdir(parents=True, exist_ok=True)

    src_conn: sqlite3.Connection | None = None
    dest_conn: sqlite3.Connection | None = None
    try:
        src_uri = f"{backup_path.resolve().as_uri()}?mode=ro"
        src_conn = sqlite3.connect(src_uri, uri=True)
        dest_conn = sqlite3.connect(target_path)
        src_conn.backup(dest_conn)
        dest_conn.execute("PRAGMA journal_mode=DELETE")
    except sqlite3.Error as exc:
        if dest_conn is not None:
            dest_conn.close()
            dest_conn = None
        if src_conn is not None:
            src_conn.close()
            src_conn = None
        _cleanup(target_path)
        raise BackupError(f"Wiederherstellung nach {target_path} fehlgeschlagen: {exc}") from exc
    finally:
        if dest_conn is not None:
            dest_conn.close()
        if src_conn is not None:
            src_conn.close()

    return target_path


def run_backup(cfg: Config, *, today: date, notify: Callable[[str], None] | None = None) -> int:
    """Führt die tägliche Sicherung nach Konfiguration aus.

    target = cfg.raw["backup"]["target_dir"]; fehlt/leer -> Warnung, Rückgabe 2, kein notify.
    Quellen: state.db_path, state.track_db_path und SC_DIGGER_CONFIG_LOCAL (falls gesetzt).
    backup_all, danach prune_backups.
    Erfolg -> 0. BackupError oder OSError -> notify / Log, Rückgabe 1.
    """
    backup_cfg = (cfg.raw.get("backup") or {}) if isinstance(cfg.raw, dict) else {}
    target_dir_val = backup_cfg.get("target_dir")
    if not target_dir_val or not str(target_dir_val).strip():
        log.warning("Sicherung nicht eingerichtet (backup.target_dir leer)")
        return 2

    target_dir = Path(target_dir_val)

    state_cfg = (cfg.raw.get("state") or {}) if isinstance(cfg.raw, dict) else {}
    sources: list[Path] = []
    if state_cfg.get("db_path"):
        sources.append(Path(state_cfg["db_path"]))
    if state_cfg.get("track_db_path"):
        sources.append(Path(state_cfg["track_db_path"]))

    local_env = os.environ.get("SC_DIGGER_CONFIG_LOCAL")
    if local_env:
        sources.append(Path(local_env))

    try:
        stamp = today.isoformat()
        backup_all(sources, target_dir, stamp=stamp)
        keep_daily = int(backup_cfg.get("keep_daily", 7))
        keep_weekly = int(backup_cfg.get("keep_weekly", 4))
        prune_backups(target_dir, keep_daily=keep_daily, keep_weekly=keep_weekly)
        return 0
    except (BackupError, OSError) as exc:
        msg = f"🚨 sc-digger: Sicherung fehlgeschlagen ({type(exc).__name__}): {exc}"
        log.error(msg)
        if notify is not None:
            notify(msg)
        return 1


def _make_telegram_notifier(cfg: Config) -> Callable[[str], None] | None:
    """Erstellt eine Notifier-Funktion für Telegram über output.telegram_call."""
    token = cfg.telegram_token
    chat_id = cfg.telegram_chat_id
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
    """CLI-Einstiegspunkt für Sicherung und Wiederherstellung."""
    install_redacting_logging(logging.INFO)

    parser = argparse.ArgumentParser(
        prog="python -m sc_digger.backup",
        description="Sicherung und Wiederherstellung der SQLite-Datenbanken",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # run
    run_parser = subparsers.add_parser("run", help="Tägliche Sicherung ausführen")
    run_parser.add_argument(
        "--config",
        default="config.yaml",
        help="Pfad zur Konfiguration (Standard: config.yaml)",
    )

    # restore
    restore_parser = subparsers.add_parser(
        "restore", help="SQLite-Datenbank aus Sicherung wiederherstellen"
    )
    restore_parser.add_argument("backup_file", help="Pfad zur gesicherten SQLite-Datei")
    restore_parser.add_argument("target", help="Zielpfad der Datenbank")
    restore_parser.add_argument(
        "--force",
        action="store_true",
        help="Vorhandene Zieldatei überschreiben (bisherige Datei wird als .vor-restore gesichert)",
    )

    args = parser.parse_args(argv)

    if args.subcommand == "run":
        try:
            cfg = Config.load(args.config)
        except Exception as exc:
            log.error("Konfiguration konnte nicht geladen werden: %s", exc)
            return 1
        notifier = _make_telegram_notifier(cfg)
        return run_backup(cfg, today=date.today(), notify=notifier)

    if args.subcommand == "restore":
        try:
            restore_sqlite(args.backup_file, args.target, force=args.force)
            return 0
        except (FileExistsError, BackupError, OSError) as exc:
            sys.stderr.write(f"FEHLER: {exc}\n")
            return 1

    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
