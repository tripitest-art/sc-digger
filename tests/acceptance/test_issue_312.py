from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _read(name: str) -> str:
    p = _ROOT / name
    assert p.is_file(), f"{name} fehlt"
    return p.read_text(encoding="utf-8")


def test_security_md_nennt_den_meldeweg():
    t = _read("SECURITY.md")
    assert "security/advisories" in t or "Private vulnerability reporting" in t
    assert "Zugangsdaten" in t
    assert "@" not in t, "keine E-Mail-Adresse im öffentlichen Repo"


def test_security_md_warnt_vor_oeffentlichen_issues():
    t = _read("SECURITY.md")
    assert "Issue" in t and "öffentlich" in t


def test_readme_verweist_auf_security_md():
    assert "SECURITY.md" in _read("README.md")


def test_changelog_nennt_security_md():
    changelog = _read("CHANGELOG.md")
    unreleased = changelog.split("## Unreleased", 1)[1].split("\n## ", 1)[0]
    assert "SECURITY.md" in unreleased
