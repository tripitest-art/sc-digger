"""Akzeptanztests: Changelog-Fragmente statt gemeinsamer Zeile."""

import importlib.util
import pathlib

CHANGELOG = """# Changelog

## Unreleased

### Geändert
- Bestehender Eintrag (#500)
"""


def _tool():
    path = pathlib.Path("scripts/changelog.py")
    assert path.exists(), "scripts/changelog.py fehlt"
    spec = importlib.util.spec_from_file_location("changelog_tool", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _setup(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    frag = tmp_path / "changelog.d"
    frag.mkdir()
    return tmp_path / "CHANGELOG.md", frag


def test_fragmente_werden_zusammengefuehrt(tmp_path):
    changelog, frag = _setup(tmp_path)
    (frag / "530.md").write_text("### Geändert\n- Energie verdrahtet (#530)\n", encoding="utf-8")
    (frag / "531.md").write_text("### Behoben\n- Etwas repariert\n", encoding="utf-8")

    geloescht = _tool().merge_fragments(changelog, frag)
    text = changelog.read_text(encoding="utf-8")

    assert "### Behoben" in text
    assert "- Energie verdrahtet (#530)" in text
    assert "- Etwas repariert (#531)" in text, "PR-Nummer fehlt im Fragment, muss aus dem Dateinamen kommen"
    assert sorted(p.name for p in geloescht) == ["530.md", "531.md"]
    assert list(frag.iterdir()) == [], "Fragmente muessen nach dem Zusammenfuehren weg sein"
    assert text.index("### Geändert") < text.index("### Behoben"), "Abschnittsreihenfolge falsch"


def test_ohne_fragmente_bleibt_die_datei_gleich(tmp_path):
    changelog, frag = _setup(tmp_path)
    vorher = changelog.read_bytes()
    assert _tool().merge_fragments(changelog, frag) == []
    assert changelog.read_bytes() == vorher


def test_pr_ohne_fragment_wird_abgelehnt():
    fehler = _tool().check_pr(["sc_digger/db.py", "tests/test_db.py"], [])
    assert len(fehler) == 1
    assert "changelog.d" in fehler[0]


def test_pr_mit_fragment_oder_label_ist_in_ordnung():
    tool = _tool()
    assert tool.check_pr(["sc_digger/db.py", "changelog.d/533.md"], []) == []
    assert tool.check_pr(["sc_digger/db.py"], ["kein-changelog"]) == []
    fehler = tool.check_pr(["CHANGELOG.md"], ["kein-changelog"])
    assert len(fehler) == 1
    assert "CHANGELOG.md" in fehler[0]
