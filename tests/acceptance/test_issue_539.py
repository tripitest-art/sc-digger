"""Akzeptanztests: Fragmente werden ueber einen PR zusammengefuehrt."""

import importlib.util
import pathlib


def _tool():
    path = pathlib.Path("scripts/changelog.py")
    assert path.exists(), "scripts/changelog.py fehlt"
    spec = importlib.util.spec_from_file_location("changelog_tool", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_zusammenfuehren_label_erlaubt_changelog_ohne_fragment():
    tool = _tool()
    assert tool.check_pr(["CHANGELOG.md"], ["changelog-zusammenfuehren"]) == []
    assert tool.check_pr(["CHANGELOG.md", "changelog.d/536.md"], ["changelog-zusammenfuehren"]) == []


def test_kein_changelog_label_erlaubt_changelog_nicht():
    fehler = _tool().check_pr(["CHANGELOG.md"], ["kein-changelog"])
    assert len(fehler) == 1
    assert "CHANGELOG.md" in fehler[0]


def test_ohne_label_bleibt_changelog_verboten():
    fehler = _tool().check_pr(["CHANGELOG.md", "changelog.d/536.md"], [])
    assert len(fehler) == 1
    assert "CHANGELOG.md" in fehler[0]
