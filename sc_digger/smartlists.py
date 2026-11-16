"""Smartlist-Regeln: Regelwerk prüfen, auf Tracks anwenden, Auswahl aus der Track-DB (Issue #564).

Eine Smartlist ist eine gespeicherte Regel über die Bibliothek („BPM 150–160, Camelot 8A/9A,
Energie 4–5, Tag ‚peak time‘“). ``parse_rules`` prüft ein dict und baut daraus ``Rules``,
``matches`` wertet die Regeln auf einer einzelnen Zeile aus und ``select_tracks`` wendet sie
auf die Track-DB an. ``matches`` braucht bewusst weder Datenbank noch Netz, damit sich die
Regel-Logik ohne Fixtures prüfen lässt. Gespeichert werden Smartlists über
``TrackDB.save_smartlist`` (Tabelle ``smartlists``).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, fields
from typing import Any, Mapping, Sequence

from .db import TrackDB, TrackRecord
from .tags import normalize_tag


class RuleError(ValueError):
    """Ungültige Regel; die Meldung nennt den Schlüssel."""


@dataclass(frozen=True)
class Rules:
    bpm_min: float | None = None
    bpm_max: float | None = None
    keys: frozenset[str] | None = None      # Camelot, z. B. {"8A", "9A"}
    energy_min: int | None = None
    energy_max: int | None = None
    taste_min: float | None = None          # 0..1
    rating_min: int | None = None           # rb_rating 0..5
    duration_min: float | None = None       # Sekunden
    duration_max: float | None = None
    tags_all: tuple[str, ...] = ()
    tags_any: tuple[str, ...] = ()


# Welche Schlüssel Kommazahlen, ganze Zahlen und Tag-Listen sind. Zusammen mit ``keys`` sind
# das genau die Felder von ``Rules``; alles andere lehnt ``parse_rules`` ab.
_FLOAT_KEYS = ("bpm_min", "bpm_max", "taste_min", "duration_min", "duration_max")
_INT_KEYS = ("energy_min", "energy_max", "rating_min")
_TAG_KEYS = ("tags_all", "tags_any")
# Paare, bei denen eine Untergrenze über der Obergrenze nie einen Track träfe.
_RANGES = (("bpm_min", "bpm_max"), ("energy_min", "energy_max"), ("duration_min", "duration_max"))

# Erlaubte Wertebereiche (Grenzen einschließlich). Ohne sie liefe ein Tippfehler wie
# "taste_min: 7" (statt 0.7) oder "rating_min: 9" still auf eine leere Liste hinaus.
_BOUNDS: dict[str, tuple[int, int]] = {
    "taste_min": (0, 1),
    "rating_min": (0, 5),
    "energy_min": (1, 5),
    "energy_max": (1, 5),
}
# BPM und Dauer haben keine feste Obergrenze, müssen aber größer als 0 sein.
_POSITIVE_KEYS = ("bpm_min", "bpm_max", "duration_min", "duration_max")

# Camelot-Key: 1..12 gefolgt von A oder B. Dient der Prüfung in ``parse_rules`` und der
# Sortierung ("10A" -> (10, "A")).
_CAMELOT_RE = re.compile(r"^([1-9]|1[0-2])([AB])$")


def _number(key: str, value: Any) -> float:
    # bool ist in Python ein int; "bpm_min: true" ist aber ein Tippfehler, keine 1.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuleError(f"{key}: Zahl erwartet, nicht {type(value).__name__}.")
    if not math.isfinite(value):
        # NaN/Unendlich ließen sich nicht als gültiges JSON speichern und träfen nie.
        raise RuleError(f"{key}: endliche Zahl erwartet, nicht {value!r}.")
    return float(value)


def _integer(key: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuleError(f"{key}: ganze Zahl erwartet, nicht {type(value).__name__}.")
    return value


def _strings(key: str, value: Any) -> list[str]:
    """Einträge einer Liste von Texten. Ein einzelner Text ist bewusst keine Liste:
    ``"8A"`` würde sonst in die Zeichen ``"8"`` und ``"A"`` zerfallen."""
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple, set, frozenset)):
        raise RuleError(f"{key}: Liste von Texten erwartet, nicht {type(value).__name__}.")
    eintraege = list(value)
    for eintrag in eintraege:
        if not isinstance(eintrag, str):
            raise RuleError(
                f"{key}: Liste von Texten erwartet, enthält aber {type(eintrag).__name__}."
            )
    return eintraege


def _tags(key: str, value: Any) -> tuple[str, ...]:
    tags: list[str] = []
    for roh in _strings(key, value):
        try:
            tag = normalize_tag(roh)
        except ValueError as exc:
            raise RuleError(f"{key}: {exc}") from exc
        # Doppelte fallen weg, die Reihenfolge der Eingabe bleibt (stabiles JSON).
        if tag not in tags:
            tags.append(tag)
    return tuple(tags)


def _keys(key: str, value: Any) -> frozenset[str] | None:
    keys: set[str] = set()
    for roh in _strings(key, value):
        # Groß/klein und Rand wie in harmonic.compatible_keys: "8a" meint "8A".
        camelot = roh.strip().upper()
        if not _CAMELOT_RE.match(camelot):
            raise RuleError(
                f"{key}: {roh!r} ist kein Camelot-Key "
                "(erlaubt: 1..12 gefolgt von A oder B, z. B. 8A oder 12B)."
            )
        keys.add(camelot)
    # Eine leere Liste heißt „keine Key-Regel“, nicht „kein Key passt“.
    return frozenset(keys) or None


def parse_rules(data: Mapping[str, Any]) -> Rules:
    """Baut Regeln aus einem dict. Unbekannter Schlüssel, falscher Typ oder
    widersprüchliche Grenzen (min > max) ⇒ RuleError mit dem Schlüssel in der Meldung.
    Tags werden über sc_digger.tags.normalize_tag normalisiert.

    Ein Wert ``None`` gilt als „nicht gesetzt“, ebenso eine leere Liste.
    Wertebereiche: ``taste_min`` 0..1, ``rating_min`` 0..5, ``energy_*`` 1..5, ``bpm_*`` und
    ``duration_*`` größer als 0, ``keys`` nur Camelot (1..12 gefolgt von A oder B).
    Ein Wert außerhalb ⇒ RuleError mit Schlüssel und erlaubtem Bereich.
    """
    if not isinstance(data, Mapping):
        raise RuleError(f"Regeln: dict erwartet, nicht {type(data).__name__}.")
    bekannt = {f.name for f in fields(Rules)}
    werte: dict[str, Any] = {}
    for key, value in data.items():
        if key not in bekannt:
            raise RuleError(
                f"Unbekannter Schlüssel {key!r} (erlaubt: {', '.join(sorted(bekannt))})."
            )
        if value is None:
            continue
        if key in _FLOAT_KEYS:
            werte[key] = _number(key, value)
        elif key in _INT_KEYS:
            werte[key] = _integer(key, value)
        elif key in _TAG_KEYS:
            werte[key] = _tags(key, value)
        else:
            werte[key] = _keys(key, value)

        if key in _BOUNDS:
            unten, oben = _BOUNDS[key]
            if not unten <= werte[key] <= oben:
                raise RuleError(
                    f"{key}: {werte[key]:g} liegt außerhalb des erlaubten Bereichs "
                    f"{unten}..{oben}."
                )
        elif key in _POSITIVE_KEYS and werte[key] <= 0:
            raise RuleError(
                f"{key}: {werte[key]:g} ist nicht erlaubt (erlaubter Bereich: größer als 0)."
            )

    for unten, oben in _RANGES:
        if unten in werte and oben in werte and werte[unten] > werte[oben]:
            raise RuleError(
                f"{unten} ({werte[unten]:g}) ist größer als {oben} ({werte[oben]:g})."
            )
    return Rules(**werte)


def _value(row: Mapping[str, Any], key: str) -> Any:
    """Wert der Spalte oder ``None``, wenn sie fehlt. Kein ``.get``: ``sqlite3.Row``
    kennt es nicht, soll hier aber genauso funktionieren wie ein dict."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _in_range(value: Any, low: float | None, high: float | None) -> bool:
    """True, wenn keine Grenze gesetzt ist oder der Wert in den (einschließlichen) Grenzen liegt.
    Ein fehlender oder nicht als Zahl lesbarer Wert passt zu keiner gesetzten Grenze."""
    if low is None and high is None:
        return True
    if value is None or isinstance(value, bool):
        return False
    try:
        zahl = float(value)
    except (TypeError, ValueError):
        return False
    if math.isnan(zahl):
        return False
    return (low is None or zahl >= low) and (high is None or zahl <= high)


def matches(row: Mapping[str, Any], rules: Rules, tags: Sequence[str] = ()) -> bool:
    """Reine Auswertung. row ist eine Zeile aus `tracks` (SELECT *), tags die Tags des Tracks.
    Fehlt ein Wert, den eine gesetzte Regel braucht (bpm/energy/taste_score/rb_rating/duration/
    key_camelot ist NULL), gilt der Track als nicht passend. Leere Regeln passen immer."""
    if not _in_range(_value(row, "bpm"), rules.bpm_min, rules.bpm_max):
        return False
    if not _in_range(_value(row, "energy"), rules.energy_min, rules.energy_max):
        return False
    if not _in_range(_value(row, "taste_score"), rules.taste_min, None):
        return False
    if not _in_range(_value(row, "rb_rating"), rules.rating_min, None):
        return False
    if not _in_range(_value(row, "duration"), rules.duration_min, rules.duration_max):
        return False
    if rules.keys:
        key = _value(row, "key_camelot")
        if not isinstance(key, str) or key.strip().upper() not in rules.keys:
            return False
    if rules.tags_all or rules.tags_any:
        vorhanden = set(tags)
        if not vorhanden.issuperset(rules.tags_all):
            return False
        if rules.tags_any and vorhanden.isdisjoint(rules.tags_any):
            return False
    return True


def _sort_key(track: TrackRecord) -> tuple[Any, ...]:
    """bpm aufsteigend (None zuletzt), dann key_camelot, dann id.

    Der Key wird in Rad-Reihenfolge sortiert (1A, 1B, 2A, … 12B): als reiner Text käme
    „10A“ vor „2A“. Keys in anderer Schreibweise folgen alphabetisch, fehlende zuletzt.
    """
    key = (track.key_camelot or "").strip().upper()
    m = _CAMELOT_RE.match(key)
    if m:
        key_teil: tuple[Any, ...] = (0, int(m.group(1)), m.group(2))
    elif key:
        key_teil = (1, 0, key)
    else:
        key_teil = (2, 0, "")
    return (track.bpm is None, track.bpm or 0.0, key_teil, track.id or 0)


def select_tracks(db: TrackDB, rules: Rules, *, limit: int | None = None) -> list[TrackRecord]:
    """Alle passenden Tracks, sortiert nach bpm aufsteigend (None zuletzt), dann key_camelot,
    dann id. Liest die Tags über die Tabelle `track_tags`."""
    if limit is not None and limit < 0:
        raise ValueError(f"limit darf nicht negativ sein: {limit}.")

    # Tags nur laden, wenn eine Regel sie braucht – dann in einer Abfrage statt je Track.
    tags: dict[int, list[str]] = {}
    if rules.tags_all or rules.tags_any:
        for zeile in db.db.execute("SELECT track_id, tag FROM track_tags"):
            tags.setdefault(int(zeile["track_id"]), []).append(str(zeile["tag"]))

    # Gefiltert wird in Python über ``matches`` statt in SQL, damit es genau eine Stelle
    # gibt, die entscheidet, was eine Regel bedeutet. ``matches`` bekommt die rohe Zeile:
    # ``TrackRecord`` führt nicht jede Spalte (z. B. ``taste_score``).
    treffer = [
        TrackRecord.from_row(zeile)
        for zeile in db.db.execute("SELECT * FROM tracks")
        if matches(zeile, rules, tags.get(int(zeile["id"]), ()))
    ]
    treffer.sort(key=_sort_key)
    return treffer if limit is None else treffer[:limit]
