"""Reine Logik des Konfigurationseditors (Issue #145): Parsen, Diff, Override-Datei.

Warum getrennt von der Web-Schicht: Das Formulieren eines Formularwerts und das Schreiben
der lokalen Override-Datei lässt sich ohne FastAPI testen. Die Web-Schicht ruft nur diese
Funktionen auf. Die versionierte `config.yaml` wird hier nie angefasst.
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import yaml

from ..schema import Field, Problem, deep_merge, get_field, validate_config


@dataclass(frozen=True)
class Change:
    path: str
    old: Any            # None, wenn der Schlüssel vorher fehlte
    new: Any            # None, wenn der Schlüssel nachher fehlt


def get_path(d: Mapping[str, Any], path: str, default: Any = None) -> Any:
    """Wert zu einem Punkt-Pfad ("scoring.weights.like_ratio"); default, wenn etwas fehlt."""
    current: Any = d
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return default
        current = current[part]
    return current


def parse_form_value(field: Field, text: str) -> Any:
    """Formulartext -> Wert nach field.kind; ValueError mit deutscher Meldung bei Fehlern."""
    kind = field.kind
    if kind == "bool":
        value = text.strip()
        if value == "true":
            return True
        if value == "false":
            return False
        raise ValueError(f"Erwartet true oder false, bekommen {text!r}.")

    if kind == "int":
        try:
            return int(text.strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Erwartet eine ganze Zahl, bekommen {text!r}.") from exc

    if kind == "float":
        # Komma oder Punkt als Dezimaltrenner zulassen (deutsche Eingabe).
        value = text.strip().replace(",", ".")
        if not value:
            raise ValueError("Erwartet eine Zahl, bekommen leeren Text.")
        try:
            if "." in value or "e" in value.lower():
                return float(value)
            return int(value)
        except ValueError as exc:
            raise ValueError(f"Erwartet eine Zahl, bekommen {text!r}.") from exc

    if kind == "str":
        return text.strip()

    if kind == "choice":
        value = text.strip()
        if value not in field.choices:
            erlaubt = ", ".join(field.choices)
            raise ValueError(f"Wert {value!r} ist nicht erlaubt (erlaubt: {erlaubt}).")
        return value

    if kind == "list":
        # Ein Eintrag je Zeile, getrimmt, leere Zeilen entfallen, Reihenfolge und Duplikate bleiben.
        return [line.strip() for line in text.splitlines() if line.strip()]

    if kind == "map":
        raise ValueError("Zuordnungen sind im Formular nicht editierbar.")

    raise ValueError(f"Unbekannter Feldtyp {kind!r}.")


def format_form_value(field: Field, value: Any) -> str:
    """Umkehrung von parse_form_value: bool -> "true"/"false", list -> Einträge mit "\\n" verbunden."""
    if field.kind == "bool":
        return "true" if value else "false"
    if field.kind == "list":
        return "\n".join(str(item) for item in value)
    return str(value)


def minimal_override(base: Mapping[str, Any], effective: Mapping[str, Any]) -> dict[str, Any]:
    """Nur die Blätter, die in effective von base abweichen.

    Zuordnungen werden rekursiv verglichen, Listen als Ganzes, neue Schlüssel eingeschlossen.
    Leere Abschnitte entfallen. Das Ergebnis teilt keine Objekte mit effective (deepcopy).
    """
    result: dict[str, Any] = {}
    for key, value in effective.items():
        if key in base:
            base_value = base[key]
            if isinstance(base_value, Mapping) and isinstance(value, Mapping):
                sub = minimal_override(base_value, value)
                if sub:
                    result[key] = sub
            elif base_value != value:
                result[key] = copy.deepcopy(value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _diff(old: Mapping[str, Any], new: Mapping[str, Any], prefix: str, out: list[Change]) -> None:
    for key in set(old) | set(new):
        path = f"{prefix}.{key}" if prefix else key
        in_old, in_new = key in old, key in new
        old_value = old.get(key)
        new_value = new.get(key)
        if in_old and in_new and isinstance(old_value, Mapping) and isinstance(new_value, Mapping):
            _diff(old_value, new_value, path, out)
        elif not in_old:
            out.append(Change(path, None, new_value))
        elif not in_new:
            out.append(Change(path, old_value, None))
        elif old_value != new_value:
            out.append(Change(path, old_value, new_value))


def diff_config(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[Change]:
    """Unterschiede auf Blatt-Ebene (Listen als Ganzes), sortiert nach path."""
    changes: list[Change] = []
    _diff(old, new, "", changes)
    return sorted(changes, key=lambda change: change.path)


def _remove(d: dict[str, Any], parts: list[str]) -> None:
    key = parts[0]
    if key not in d:
        return
    if len(parts) == 1:
        del d[key]
        return
    child = d[key]
    if not isinstance(child, dict):
        return
    _remove(child, parts[1:])
    if not child:
        # Leer gewordene Elternabschnitte mit entfernen.
        del d[key]


def remove_key(local: Mapping[str, Any], path: str) -> dict[str, Any]:
    """Kopie von local ohne den Punkt-Pfad; leer gewordene Elternabschnitte entfallen."""
    result = copy.deepcopy(dict(local))
    _remove(result, path.split("."))
    return result


def _set_path(d: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = d
    for part in parts[:-1]:
        nxt = current.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            current[part] = nxt
        current = nxt
    current[parts[-1]] = value


def apply_form(base: Mapping[str, Any], local: Mapping[str, Any],
               form: Mapping[str, str]) -> tuple[dict[str, Any], list[Problem]]:
    """Wendet Formularwerte (Schlüssel = Punkt-Pfad) auf deep_merge(base, local) an.

    Liefert (neue lokale Datei, Probleme). Unbekannte, nicht editierbare oder nicht parsebare
    Werte ergeben ein Problem der Stufe "error". Danach wird die zusammengeführte Config
    validiert (Fehler und Warnungen gehören zu den Problemen). Gibt es mindestens einen Fehler,
    ist die neue lokale Datei eine unveränderte Kopie von local. Sonst ist sie der minimale
    Override gegenüber base.
    """
    merged = deep_merge(base, local)
    problems: list[Problem] = []

    for path, text in form.items():
        field = get_field(path)
        if field is None:
            problems.append(Problem("error", path, f"Unbekanntes Feld {path!r}."))
            continue
        if not field.editable:
            problems.append(Problem("error", path, "Dieses Feld ist nicht editierbar."))
            continue
        try:
            value = parse_form_value(field, text)
        except ValueError as exc:
            problems.append(Problem("error", path, str(exc)))
            continue
        _set_path(merged, path, value)

    problems.extend(validate_config(merged))

    if any(problem.level == "error" for problem in problems):
        return copy.deepcopy(dict(local)), problems
    return minimal_override(base, merged), problems


_HEADER = (
    "# Lokale Override-Datei für sc-digger (nur Abweichungen von config.yaml).\n"
    "# Wird vom Web-Konfigurationseditor gepflegt; nicht von Hand mit config.yaml verwechseln.\n"
)


def _write_atomically(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _backup_name(path: Path, now: datetime) -> str:
    return f"{path.name}.bak-{now:%Y%m%d-%H%M%S}"


def _prune_backups(path: Path, keep: int) -> None:
    backups = sorted(
        p for p in path.parent.iterdir()
        if p.name.startswith(path.name + ".bak-")
    )
    for old in backups[:-keep] if keep > 0 else backups:
        old.unlink()


def save_local(path: Path, local: Mapping[str, Any], *, keep_backups: int = 10,
               now: datetime | None = None) -> Path | None:
    """Schreibt local als YAML atomar nach path, mit Kopfkommentar und Backup der Vorfassung.

    Existiert path schon, wird die vorherige Fassung als f"{path.name}.bak-{now:%Y%m%d-%H%M%S}"
    gesichert und deren Pfad zurückgegeben, sonst None. Ist local leer, wird die Datei (nach dem
    Backup) gelöscht bzw. gar nicht erst angelegt. Danach bleiben nur die keep_backups neuesten
    Backups (Sortierung nach Namen).
    """
    path = Path(path)
    moment = now if now is not None else datetime.now()

    backup: Path | None = None
    if path.exists():
        backup = path.with_name(_backup_name(path, moment))
        backup.write_bytes(path.read_bytes())

    if local:
        text = _HEADER + yaml.safe_dump(dict(local), allow_unicode=True, sort_keys=False)
        _write_atomically(path, text)
    elif path.exists():
        path.unlink()

    _prune_backups(path, keep_backups)
    return backup
