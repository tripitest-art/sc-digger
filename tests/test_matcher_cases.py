"""Messlatte für den Track-/Sammlungs-Matcher mit Problemfällen.

Die Fälle stehen als Daten in ``tests/fixtures/match_cases.yaml``, damit neue
Problemfälle ohne Python-Kenntnisse ergänzt werden können. Ausgewertet wird mit
denselben Heuristiken wie der echte Abgleich (``sc_digger/collection.py``), so hält
die Liste vor einer Optimierung den Ist-Stand fest.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from sc_digger.collection import name_key, names_match

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "match_cases.yaml"

# Transcodes und Streams weichen um wenige Sekunden ab; erst darüber ist es ein
# anderes Stück (z. B. Extended gegen Original).
DURATION_TOLERANCE_S = 5.0


@dataclass(frozen=True)
class MatchCase:
    id: str
    category: str  # z. B. 'remix', 'unicode', 'feat', 'duration', 'edit_in_library', 'noise'
    query_artist: str | None
    query_title: str
    query_duration_s: float | None
    target_artist: str | None
    target_title: str
    target_duration_s: float | None
    expected_match: bool
    note: str = ""


def _opt_float(value: object) -> float | None:
    return None if value is None else float(value)


def load_match_cases(path: Path | str | None = None) -> list[MatchCase]:
    """Lädt Testfälle aus YAML. Standard-Pfad: tests/fixtures/match_cases.yaml.

    Wirft FileNotFoundError, wenn die Datei nicht existiert.
    """
    fixture = Path(path) if path is not None else FIXTURE_PATH
    if not fixture.is_file():
        raise FileNotFoundError(f"Testfalldatei fehlt: {fixture}")
    raw_cases = yaml.safe_load(fixture.read_text(encoding="utf-8")) or []
    if not isinstance(raw_cases, list):
        raise ValueError(f"{fixture}: erwartet eine Liste von Testfällen")
    return [
        MatchCase(
            id=str(raw["id"]),
            category=str(raw["category"]),
            query_artist=raw.get("query_artist"),
            query_title=str(raw["query_title"]),
            query_duration_s=_opt_float(raw.get("query_duration_s")),
            target_artist=raw.get("target_artist"),
            target_title=str(raw["target_title"]),
            target_duration_s=_opt_float(raw.get("target_duration_s")),
            expected_match=bool(raw["expected_match"]),
            note=str(raw.get("note", "")),
        )
        for raw in raw_cases
    ]


def evaluate_match_case(case: MatchCase) -> bool:
    """Wertet einen Testfall mit name_key, names_match und Dauer-Toleranz (5.0s) aus.

    Gibt True zurück, wenn Name und Dauer (sofern beide angegeben) übereinstimmen.
    """
    if not names_match(
        name_key(case.query_artist, case.query_title),
        name_key(case.target_artist, case.target_title),
    ):
        return False
    if case.query_duration_s is not None and case.target_duration_s is not None:
        delta = abs(case.query_duration_s - case.target_duration_s)
        if delta > DURATION_TOLERANCE_S:
            return False
    return True


@pytest.mark.parametrize("case", load_match_cases(), ids=lambda c: c.id)
def test_match_case(case: MatchCase) -> None:
    """Jeder Fall aus der YAML-Datei muss seinem erwarteten Ergebnis entsprechen."""
    actual = evaluate_match_case(case)
    assert actual == case.expected_match, (
        f"Testfall {case.id} ({case.category}): erwartet {case.expected_match}, "
        f"erhalten {actual}. Notiz: {case.note}"
    )
