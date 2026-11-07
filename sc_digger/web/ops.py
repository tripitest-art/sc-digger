from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sc_digger.redact import redact


@dataclass(frozen=True)
class OpsItem:
    key: str      # "disk", "backup" oder "canary"
    label: str    # "Speicherplatz", "Sicherung", "API-Kanarienvogel"
    state: str    # "ok", "warn" oder "off"
    text: str


def _cfg_get(cfg: Any, section: str, default: Any = None) -> Any:
    if hasattr(cfg, "raw") and isinstance(cfg.raw, dict):
        return cfg.raw.get(section, default)
    if isinstance(cfg, dict):
        return cfg.get(section, default)
    try:
        return cfg[section]
    except Exception:
        return default


def _collect_disk(cfg: Any, usage: Any) -> OpsItem:
    download_sec = _cfg_get(cfg, "download", {})
    if not isinstance(download_sec, dict):
        download_sec = {}
    min_free_gb = download_sec.get("min_free_gb")
    if min_free_gb is None or min_free_gb <= 0:
        return OpsItem(
            key="disk",
            label="Speicherplatz",
            state="off",
            text="Wächter aus (download.min_free_gb = 0)",
        )

    inbox_dir = download_sec.get("inbox_dir", ".")
    try:
        u = usage(inbox_dir)
        free_gb = u.free / (1024 ** 3)
        if free_gb >= min_free_gb:
            return OpsItem(
                key="disk",
                label="Speicherplatz",
                state="ok",
                text=f"{free_gb:.1f} GB frei (Minimum {min_free_gb:g} GB)",
            )
        else:
            return OpsItem(
                key="disk",
                label="Speicherplatz",
                state="warn",
                text=f"Nur {free_gb:.1f} GB frei (Minimum {min_free_gb:g} GB)",
            )
    except OSError:
        return OpsItem(
            key="disk",
            label="Speicherplatz",
            state="warn",
            text="Messung fehlgeschlagen",
        )
    except Exception:
        return OpsItem(
            key="disk",
            label="Speicherplatz",
            state="warn",
            text="Messung fehlgeschlagen",
        )


def _collect_backup(cfg: Any, now: datetime | None) -> OpsItem:
    backup_sec = _cfg_get(cfg, "backup", {})
    if not isinstance(backup_sec, dict):
        backup_sec = {}
    target_dir_val = backup_sec.get("target_dir")
    if not target_dir_val or not str(target_dir_val).strip():
        return OpsItem(
            key="backup",
            label="Sicherung",
            state="off",
            text="nicht eingerichtet",
        )

    target_dir = Path(target_dir_val)
    if not target_dir.is_dir():
        return OpsItem(
            key="backup",
            label="Sicherung",
            state="warn",
            text="Keine Sicherung gefunden",
        )

    date_dirs: list[date] = []
    try:
        for entry in target_dir.iterdir():
            if entry.is_dir():
                try:
                    d = date.fromisoformat(entry.name)
                    date_dirs.append(d)
                except ValueError:
                    continue
    except OSError:
        return OpsItem(
            key="backup",
            label="Sicherung",
            state="warn",
            text="Keine Sicherung gefunden",
        )

    if not date_dirs:
        return OpsItem(
            key="backup",
            label="Sicherung",
            state="warn",
            text="Keine Sicherung gefunden",
        )

    latest_date = max(date_dirs)
    now_date = (now or datetime.now(timezone.utc)).date()
    alter = (now_date - latest_date).days
    state = "ok" if alter <= 2 else "warn"
    if alter == 0:
        suffix = "heute"
    elif alter == 1:
        suffix = "gestern"
    else:
        suffix = f"vor {alter} Tagen"
    return OpsItem(
        key="backup",
        label="Sicherung",
        state=state,
        text=f"Letzte Sicherung: {latest_date.isoformat()} ({suffix})",
    )


def _collect_canary(cfg: Any) -> OpsItem:
    state_sec = _cfg_get(cfg, "state", {})
    if not isinstance(state_sec, dict):
        state_sec = {}
    db_path = state_sec.get("db_path")
    if not db_path:
        return OpsItem(
            key="canary",
            label="API-Kanarienvogel",
            state="off",
            text="noch kein Lauf",
        )

    p = Path(db_path)
    if not p.is_file():
        return OpsItem(
            key="canary",
            label="API-Kanarienvogel",
            state="off",
            text="noch kein Lauf",
        )

    uri = f"file:{p.resolve().as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as db:
            tbl = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='runs'"
            ).fetchone()
            if not tbl:
                return OpsItem(
                    key="canary",
                    label="API-Kanarienvogel",
                    state="off",
                    text="noch kein Lauf",
                )
            row = db.execute(
                "SELECT ok, error, finished_at FROM runs WHERE mode = 'api_schema' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not row:
                return OpsItem(
                    key="canary",
                    label="API-Kanarienvogel",
                    state="off",
                    text="noch kein Lauf",
                )
            ok, error, finished_at = row
            if ok:
                return OpsItem(
                    key="canary",
                    label="API-Kanarienvogel",
                    state="ok",
                    text=f"API-Antwort vollständig (Stand {finished_at})",
                )
            else:
                err_text = redact(str(error)) if error and str(error).strip() else ""
                msg = f"Felder fehlen: {err_text}" if err_text else "Felder fehlen"
                return OpsItem(
                    key="canary",
                    label="API-Kanarienvogel",
                    state="warn",
                    text=msg,
                )
    except Exception:
        return OpsItem(
            key="canary",
            label="API-Kanarienvogel",
            state="off",
            text="noch kein Lauf",
        )


def collect_ops(cfg: Any, *, usage: Any = shutil.disk_usage, now: datetime | None = None) -> list[OpsItem]:
    """Immer drei Einträge in der Reihenfolge disk, backup, canary. Wirft nie, nur lesend.

    disk:   download.min_free_gb == 0 oder fehlt -> off, "Wächter aus (download.min_free_gb = 0)".
            Sonst Ermittlung des freien Speichers: free_gb = usage(inbox_dir).free / (1024 ** 3).
            free_gb >= min_free_gb -> ok, f"{free_gb:.1f} GB frei (Minimum {min_free_gb:g} GB)"
            sonst warn, f"Nur {free_gb:.1f} GB frei (Minimum {min_free_gb:g} GB)".
            OSError -> warn, "Messung fehlgeschlagen".
    backup: backup.target_dir leer oder fehlt -> off, "nicht eingerichtet".
            Sonst Unterordner von target_dir durchsuchen, deren Name ein ISO-Datum (YYYY-MM-DD) ist.
            now_date = (now or datetime.now(timezone.utc)).date()
            Kein solcher Ordner oder target_dir fehlt / existiert nicht: warn, "Keine Sicherung gefunden".
            Neuester Ordner -> latest_date.
            alter = (now_date - latest_date).days.
            alter <= 2 -> ok, sonst warn.
            suffix: alter == 0 -> "heute", alter == 1 -> "gestern", sonst f"vor {alter} Tagen".
            text: f"Letzte Sicherung: {latest_date.isoformat()} ({suffix})".
    canary: letzte Zeile in Tabelle runs der State-DB (state.db_path) mit mode = 'api_schema'.
            Rein lesend (mode=ro / uri=True). Fehlende DB oder Tabelle oder keine Zeile -> off, "noch kein Lauf".
            ok = 1 -> ok, f"API-Antwort vollständig (Stand {finished_at})".
            ok = 0 -> warn, f"Felder fehlen: {redact(error)}" falls error vorhanden/nicht-leer, sonst "Felder fehlen".
    """
    try:
        disk_item = _collect_disk(cfg, usage)
    except Exception:
        disk_item = OpsItem("disk", "Speicherplatz", "warn", "Messung fehlgeschlagen")

    try:
        backup_item = _collect_backup(cfg, now)
    except Exception:
        backup_item = OpsItem("backup", "Sicherung", "warn", "Keine Sicherung gefunden")

    try:
        canary_item = _collect_canary(cfg)
    except Exception:
        canary_item = OpsItem("canary", "API-Kanarienvogel", "off", "noch kein Lauf")

    return [disk_item, backup_item, canary_item]
