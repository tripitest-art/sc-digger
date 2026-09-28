"""Tests für .github/scripts/acceptance_guard.py (reine Logik, kein Netzwerk)."""

import importlib.util
import sys
from pathlib import Path

import pytest

# Das Skript liegt bewusst unter .github/ (geschützt) und ist kein Paket, daher per Pfad laden.
# Registrierung in sys.modules ist nötig, sonst scheitern die Dataclasses beim Import.
_PATH = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "acceptance_guard.py"
_spec = importlib.util.spec_from_file_location("acceptance_guard", _PATH)
g = importlib.util.module_from_spec(_spec)
sys.modules["acceptance_guard"] = g
_spec.loader.exec_module(g)

ACCEPT = "def test_x():\n    assert 1 + 1 == 2"

# So rendert GitHub ein Issue-Formular mit render: python
FORM_BODY = f"""### Ziel

Etwas besser machen.

### Akzeptanztests

```python
{ACCEPT}
```

### Fertig, wenn

- [ ] grün
"""


def _file(name, status="modified", patch="", old=None):
    d = {"filename": name, "status": status, "patch": patch}
    if old:
        d["previous_filename"] = old
    return d


def _reader(files: dict):
    return lambda p: files.get(p)


# ---------------- Parsing ----------------
@pytest.mark.parametrize("body, expected", [
    ("Closes #8", [8]),
    ("fixes #3 und resolves: #7", [3, 7]),
    ("Closed #2, closes #2", [2]),
    ("Siehe #9 (nur erwähnt)", []),
    (None, []),
])
def test_linked_issues(body, expected):
    assert g.linked_issues(body) == expected


def test_extract_block_from_issue_form():
    assert g.extract_acceptance_block(FORM_BODY) == ACCEPT


def test_extract_block_empty_form_field_is_none():
    body = "### Akzeptanztests\n\n_No response_\n\n### Kontext\n\nx"
    assert g.extract_acceptance_block(body) is None


def test_extract_block_ignores_code_of_other_sections():
    body = "### Schnittstellen\n\n```python\ndef f(): ...\n```\n\n### Akzeptanztests\n\n_No response_\n"
    assert g.extract_acceptance_block(body) is None


def test_extract_block_handwritten_h2_and_crlf():
    body = "## Akzeptanztests\r\n\r\n```python\r\n" + ACCEPT.replace("\n", "\r\n") + "\r\n```\r\n"
    assert g.extract_acceptance_block(body) == ACCEPT


def test_heading_inside_code_does_not_end_section():
    code = "# Kommentar im Test\ndef test_y():\n    pass"
    body = f"### Akzeptanztests\n\n```python\n{code}\n```\n"
    assert g.extract_acceptance_block(body) == code


def test_normalize_ignores_trailing_whitespace_and_line_endings():
    assert g.normalize("a  \r\nb\r\n\r\n") == g.normalize("a\nb")


def test_added_skip_markers_only_counts_added_lines():
    patch = "\n".join([
        "+++ b/tests/test_a.py",
        "+@pytest.mark.skip(reason='kaputt')",
        "-@pytest.mark.xfail",
        " pytest.skip('schon da')",
        "+    pytest.importorskip('librosa')",
        "+def test_skips_deleted_tracks():",
    ])
    assert g.added_skip_markers(patch) == [
        "@pytest.mark.skip(reason='kaputt')",
        "pytest.importorskip('librosa')",
    ]


# ---------------- Bewertung ----------------
def test_clean_worker_pr_passes():
    files = [
        _file("sc_digger/organize.py"),
        _file("tests/acceptance/test_issue_8.py", "added"),
    ]
    res = g.evaluate(files, set(), {8: FORM_BODY},
                     _reader({"tests/acceptance/test_issue_8.py": ACCEPT + "\n"}))
    assert res.ok, res.violations


def test_missing_acceptance_file_fails_even_with_override():
    res = g.evaluate([_file("sc_digger/organize.py")], {g.OVERRIDE_LABEL}, {8: FORM_BODY}, _reader({}))
    assert not res.ok
    assert "fehlt" in res.violations[0]


def test_weakened_acceptance_test_fails_with_diff():
    weakened = "def test_x():\n    assert True"
    res = g.evaluate([_file("tests/acceptance/test_issue_8.py", "added")], set(), {8: FORM_BODY},
                     _reader({"tests/acceptance/test_issue_8.py": weakened}))
    assert not res.ok
    assert "+    assert True" in res.violations[0]


def test_issue_without_acceptance_block_is_only_a_note():
    res = g.evaluate([_file("sc_digger/x.py")], set(), {5: "### Ziel\n\nx"}, _reader({}))
    assert res.ok and res.notes


@pytest.mark.parametrize("entry", [
    _file("tests/acceptance/test_issue_3.py", "modified"),
    _file("tests/acceptance/test_issue_3.py", "removed"),
    _file("tests/test_old.py", "renamed", old="tests/acceptance/test_issue_3.py"),
])
def test_existing_acceptance_tests_are_immutable(entry):
    assert not g.evaluate([entry], set(), {}, _reader({})).ok


@pytest.mark.parametrize("name", [
    ".github/workflows/tests.yml",
    ".github/scripts/acceptance_guard.py",
    "tests/conftest.py",
    "conftest.py",
    "pytest.ini",
    "pyproject.toml",
])
def test_infra_changes_need_override(name):
    assert not g.evaluate([_file(name)], set(), {}, _reader({})).ok
    res = g.evaluate([_file(name)], {g.OVERRIDE_LABEL}, {}, _reader({}))
    assert res.ok and "Erlaubt" in res.notes[0]


def test_new_skip_in_tests_needs_override():
    f = _file("tests/test_core.py", patch="+@pytest.mark.xfail(reason='später')")
    assert not g.evaluate([f], set(), {}, _reader({})).ok
    assert g.evaluate([f], {g.OVERRIDE_LABEL}, {}, _reader({})).ok


def test_ordinary_code_and_test_changes_pass():
    files = [
        _file("sc_digger/pipeline.py", patch="+x = 1"),
        _file("tests/test_core.py", patch="+def test_new():\n+    assert True"),
        _file("README.md"),
    ]
    assert g.evaluate(files, set(), {}, _reader({})).ok
