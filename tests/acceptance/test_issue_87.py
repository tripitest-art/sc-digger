"""Akzeptanztests: acceptance-guard erkennt Änderungen am Issue-Text nach der Übernahme."""

import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "acceptance_guard.py"
_spec = importlib.util.spec_from_file_location("acceptance_guard_issue_edit", _PATH)
g = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = g
_spec.loader.exec_module(g)

ACCEPT = "def test_x():\n    assert 1 + 1 == 2"
BODY = f"### Ziel\n\nx\n\n### Akzeptanztests\n\n```python\n{ACCEPT}\n```\n"
CLAIMED = "2026-09-01T10:00:00Z"


def _evaluate(meta, labels=frozenset(), body=BODY):
    return g.evaluate(
        files=[{"filename": "tests/acceptance/test_issue_7.py", "status": "added", "patch": ""}],
        labels=set(labels),
        issues={7: body},
        read_head_file=lambda p: ACCEPT if p == "tests/acceptance/test_issue_7.py" else None,
        issue_meta=meta,
    )


def test_never_edited_is_fine():
    assert g.issue_edit_violation(7, None, CLAIMED) is None


def test_edit_before_claim_is_fine():
    assert g.issue_edit_violation(7, "2026-09-01T09:59:59Z", CLAIMED) is None


def test_edit_after_claim_is_a_violation():
    msg = g.issue_edit_violation(7, "2026-09-01T10:00:01Z", CLAIMED)
    assert msg is not None
    assert "#7" in msg and "in-arbeit" in msg


def test_never_claimed_is_a_violation():
    msg = g.issue_edit_violation(7, None, None)
    assert msg is not None
    assert "#7" in msg and "in-arbeit" in msg


def test_timezones_are_compared_as_instants():
    # 11:30+02:00 ist 09:30Z, also vor der Übernahme um 10:00Z.
    assert g.issue_edit_violation(7, "2026-09-01T11:30:00+02:00", CLAIMED) is None
    # 12:30+02:00 ist 10:30Z, also danach.
    assert g.issue_edit_violation(7, "2026-09-01T12:30:00+02:00", CLAIMED) is not None


def test_evaluate_fails_when_issue_edited_after_claim():
    res = _evaluate({7: ("2026-09-02T08:00:00Z", CLAIMED)})
    assert not res.ok
    assert any("#7" in v for v in res.violations)


def test_override_label_turns_edit_violation_into_note():
    res = _evaluate({7: ("2026-09-02T08:00:00Z", CLAIMED)}, labels={"freigabe-geschützt"})
    assert res.ok
    assert any("freigabe-geschützt" in n and "#7" in n for n in res.notes)


def test_evaluate_passes_when_edit_was_before_claim():
    res = _evaluate({7: ("2026-08-31T08:00:00Z", CLAIMED)})
    assert res.ok, res.violations


def test_issue_without_acceptance_block_is_not_checked():
    res = _evaluate({7: ("2026-09-02T08:00:00Z", CLAIMED)}, body="### Ziel\n\nnur Doku\n")
    assert res.ok, res.violations


def test_evaluate_without_issue_meta_keeps_old_behaviour():
    res = g.evaluate(
        files=[{"filename": "tests/acceptance/test_issue_7.py", "status": "added", "patch": ""}],
        labels=set(),
        issues={7: BODY},
        read_head_file=lambda p: ACCEPT,
    )
    assert res.ok, res.violations
