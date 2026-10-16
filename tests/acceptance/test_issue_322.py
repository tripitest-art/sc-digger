"""Akzeptanztests: Testliste für den Track- und Sammlungs-Matcher mit Problemfällen."""
from pathlib import Path
import pytest
from tests.test_matcher_cases import MatchCase, evaluate_match_case, load_match_cases


def test_load_match_cases_reads_default_fixture():
    cases = load_match_cases()
    assert len(cases) >= 5
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids)), "IDs der Testfälle müssen eindeutig sein"
    for c in cases:
        assert isinstance(c, MatchCase)
        assert c.id and c.category and c.query_title and c.target_title
        assert isinstance(c.expected_match, bool)


def test_evaluate_match_case_unicode_and_umlauts():
    case = MatchCase(
        id="case-umlaut",
        category="unicode",
        query_artist="Überrest",
        query_title="Für immer",
        query_duration_s=210.0,
        target_artist="Ueberrest",
        target_title="Fuer Immer",
        target_duration_s=210.0,
        expected_match=True,
    )
    assert evaluate_match_case(case) is True


def test_evaluate_match_case_different_edits():
    case = MatchCase(
        id="case-edits",
        category="remix",
        query_artist="Ueberrest",
        query_title="Surrender (DJØ Edit)",
        query_duration_s=210.0,
        target_artist="Ueberrest",
        target_title="Surrender (VRTGØ Edit)",
        target_duration_s=210.0,
        expected_match=False,
    )
    assert evaluate_match_case(case) is False


def test_evaluate_match_case_duration_mismatch():
    case = MatchCase(
        id="case-duration",
        category="duration",
        query_artist="Ueberrest",
        query_title="Surrender",
        query_duration_s=210.0,
        target_artist="Ueberrest",
        target_title="Surrender",
        target_duration_s=390.0,
        expected_match=False,
    )
    assert evaluate_match_case(case) is False


def test_fixture_cases_match_expectations():
    cases = load_match_cases()
    for case in cases:
        actual = evaluate_match_case(case)
        assert actual == case.expected_match, (
            f"Testfall {case.id} ({case.category}) fehlgeschlagen: "
            f"erwartet {case.expected_match}, erhalten {actual}. Notiz: {case.note}"
        )