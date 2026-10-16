from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _read(name: str) -> str:
    p = _ROOT / name
    assert p.is_file(), f"{name} fehlt"
    return p.read_text(encoding="utf-8")


def test_changelog_hat_unreleased_und_nennt_die_regeln():
    t = _read("CHANGELOG.md")
    assert t.startswith("# Changelog")
    assert "## Unreleased" in t
    assert "update.sh" in t, "der Kopf nennt, dass Stephan vor dem Deploy hier liest"


def test_agents_md_hat_den_abschnitt_doku_und_changelog():
    t = _read("AGENTS.md")
    assert "## Doku und Changelog" in t
    abschnitt = t.split("## Doku und Changelog", 1)[1].split("\n## ", 1)[0]
    assert "CHANGELOG.md" in abschnitt and "Unreleased" in abschnitt
    assert "README.md" in abschnitt


def test_readme_verweist_auf_den_changelog():
    assert "CHANGELOG.md" in _read("README.md")


def test_doku_test_prueft_den_changelog():
    t = _read("tests/test_doku.py")
    assert "CHANGELOG" in t and "Unreleased" in t
