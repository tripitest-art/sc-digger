from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_CFG = _ROOT / ".github" / "dependabot.yml"


def _text() -> str:
    assert _CFG.is_file(), ".github/dependabot.yml fehlt"
    return _CFG.read_text(encoding="utf-8")


def test_version_und_beide_oekosysteme():
    t = _text()
    assert "version: 2" in t
    assert 'package-ecosystem: "github-actions"' in t
    assert 'package-ecosystem: "pip"' in t
    assert t.count('directory: "/"') == 2


def test_wochentakt_und_limit_je_oekosystem():
    t = _text()
    assert t.count('interval: "weekly"') == 2
    assert t.count("open-pull-requests-limit: 2") == 2


def test_kein_docker_oekosystem():
    assert "docker" not in _text()


def test_changelog_nennt_dependabot():
    changelog = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = changelog.split("## Unreleased", 1)[1].split("\n## ", 1)[0]
    assert "Dependabot" in unreleased
