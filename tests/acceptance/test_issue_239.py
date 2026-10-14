"""Akzeptanztests: Preflight für Worker-Issues (sc_digger.issuecheck)."""
import textwrap
from pathlib import Path

import pytest

from sc_digger import issuecheck as ic

GOOD = textwrap.dedent('''\
    from sc_digger.neu import f


    def test_f():
        assert f() == 1
''')

SECTIONS = ["Ziel", "Betroffene Dateien", "Schnittstellen", "Nicht Teil dieser Aufgabe", "Akzeptanztests",
            "Fertig, wenn", "Berührt sc_digger/main.py", "Merge-Modus", "Kontext"]


def issue(block=GOOD, merge="automatisch nach Review", fehlt=(), fence="python"):
    body = {
        "Ziel": "Etwas bauen.",
        "Betroffene Dateien": "- `sc_digger/neu.py`",
        "Schnittstellen": "```python\ndef f() -> int: ...\n```",
        "Nicht Teil dieser Aufgabe": "- nichts anderes",
        "Akzeptanztests": f"```{fence}\n{block}```" if block is not None else "_No response_",
        "Fertig, wenn": "- [ ] Tests grün",
        "Berührt sc_digger/main.py": "nein",
        "Merge-Modus": merge,
        "Kontext": "Planer: Claude.",
    }
    return "\n\n".join(f"### {name}\n\n{body[name]}" for name in SECTIONS if name not in fehlt) + "\n"


def codes(findings):
    return sorted(f.code for f in findings)


def test_gueltiges_issue_hat_keine_befunde():
    assert ic.check_static(issue()) == []


def test_extract_acceptance_block():
    assert ic.extract_acceptance_block(issue()) == GOOD.strip("\n")
    assert ic.extract_acceptance_block(issue(block=None)) is None
    assert ic.extract_acceptance_block("kein Issue") is None


@pytest.mark.parametrize("name", SECTIONS)
def test_fehlende_ueberschrift_ist_fehler(name):
    found = ic.check_static(issue(fehlt=(name,)))
    hit = [f for f in found if f.code == "ueberschrift-fehlt"]
    assert len(hit) == 1 and hit[0].level == "fehler" and name in hit[0].text


def test_kein_oder_mehrere_bloecke():
    assert codes(ic.check_static(issue(block=None))) == ["kein-block"]
    zwei = GOOD + "```\n\nText\n\n```python\ndef test_b():\n    assert True\n"
    assert "mehrere-bloecke" in codes(ic.check_static(issue(block=zwei)))


def test_syntaxfehler_und_kein_test():
    assert codes(ic.check_static(issue(block="def test_x(:\n    pass\n"))) == ["syntaxfehler"]
    assert codes(ic.check_static(issue(block="from sc_digger.neu import f\n\ndef helfer():\n    return f()\n"))) == ["kein-test"]


@pytest.mark.parametrize("zeile", ["import requests", "import socket", "import random", "from urllib import request",
                                   "import httpx", "from http import client"])
def test_verbotene_imports(zeile):
    block = f"{zeile}\n\n\ndef test_x():\n    assert True\n"
    found = ic.check_static(issue(block=block))
    assert [f.level for f in found if f.code == "verbotener-import"] == ["fehler"]


@pytest.mark.parametrize("zeile", ["time.sleep(1)", "time.time()", "datetime.now()", "datetime.datetime.utcnow()",
                                   "random.random()"])
def test_verbotene_aufrufe(zeile):
    block = f"import datetime\nimport time\nfrom datetime import datetime as dt\n\n\ndef test_x():\n    {zeile}\n"
    found = ic.check_static(issue(block=block.replace("datetime.now()", "dt.now()")))
    assert "verbotener-aufruf" in codes(found)


@pytest.mark.parametrize("zeile", ["@pytest.mark.skip\n", "@pytest.mark.skipif(True, reason='x')\n",
                                   "@pytest.mark.xfail\n", ""])
def test_skip_und_xfail(zeile):
    body = "    pytest.skip('später')\n" if not zeile else "    assert True\n"
    block = f"import pytest\n\n\n{zeile}def test_x():\n{body}"
    assert "skip-xfail" in codes(ic.check_static(issue(block=block)))
    assert "skip-xfail" in codes(ic.check_static(issue(block="import pytest\n\nx = pytest.importorskip('numpy')\n\n\ndef test_x():\n    assert x\n")))


def test_musik_pfad_ist_fehler():
    block = 'def test_x():\n    assert open("/music/Schranz/a.wav")\n'
    assert "musik-pfad" in codes(ic.check_static(issue(block=block)))


def test_erlaubte_imports_ohne_befund():
    block = ("import json\nfrom pathlib import Path\n\nimport pytest\n\nfrom sc_digger.neu import f\n"
             "from tests.test_modes import CFG, mk\n\n\ndef test_x(tmp_path):\n    assert f() == 1\n")
    assert ic.check_static(issue(block=block)) == []


def test_fremde_bibliothek_ist_nur_warnung():
    found = ic.check_static(issue(block="import mutagen\n\n\ndef test_x():\n    assert mutagen\n"))
    assert [(f.code, f.level) for f in found] == [("fremder-import", "warnung")]


@pytest.mark.parametrize("merge,ok", [("automatisch", True), ("automatisch nach Review", True),
                                      ("manuell", True), ("manuell durch Stephan (Betrieb)", True),
                                      ("vielleicht", False), ("", False)])
def test_merge_modus(merge, ok):
    assert (ic.check_static(issue(merge=merge)) == []) is ok
    if not ok:
        assert "merge-modus" in codes(ic.check_static(issue(merge=merge)))


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "sc_digger").mkdir(parents=True)
    (r / "tests").mkdir()
    (r / "sc_digger" / "__init__.py").write_text("")
    (r / "sc_digger" / "vorhanden.py").write_text("def f():\n    return 1\n")
    (r / "tests" / "__init__.py").write_text("")
    return r


def tree(path):
    return sorted(str(p.relative_to(path)) for p in path.rglob("*"))


def test_rot_ohne_umsetzung_ist_gut(repo):
    vorher = tree(repo)
    assert ic.check_red(issue(), repo) == []             # sc_digger.neu gibt es nicht: ImportError
    assert tree(repo) == vorher                          # fasst das Repo nicht an


def test_schon_gruen_ist_fehler(repo):
    block = "def test_x():\n    assert 1 + 1 == 2\n"
    found = ic.check_red(issue(block=block), repo)
    assert [(f.code, f.level) for f in found] == [("schon-gruen", "fehler")]


def test_teilweise_gruen_ist_warnung(repo):
    block = ("from sc_digger.vorhanden import f\n\n\ndef test_alt():\n    assert f() == 1\n\n\n"
             "def test_neu():\n    assert f() == 2\n")
    found = ic.check_red(issue(block=block), repo)
    assert [(f.code, f.level) for f in found] == [("teilweise-gruen", "warnung")]


def test_zeitueberschreitung_ist_ein_befund_keine_ausnahme(repo):
    found = ic.check_red(issue(), repo, timeout=0.01)
    assert [(f.code, f.level) for f in found] == [("timeout", "fehler")]


def test_validate_bricht_bei_statischen_fehlern_vor_dem_testlauf_ab(tmp_path):
    found = ic.validate(issue(block="def test_x(:\n    pass\n"), tmp_path / "gibt-es-nicht")
    assert codes(found) == ["syntaxfehler"]


def test_validate_ohne_testlauf_ist_nur_static(repo):
    assert ic.validate(issue(), repo, run_tests=False) == []
    assert ic.validate(issue(), repo) == []              # rot -> ok


def test_report_text():
    f = [ic.Finding("fehler", "kein-test", "Kein Test"), ic.Finding("warnung", "fremder-import", "yaml")]
    text = ic.format_report(f)
    assert "❌" in text and "⚠️" in text and text.rstrip().endswith("Nicht einstellen: 1 Fehler.")
    assert ic.format_report([]).rstrip().endswith("Akzeptanztests sind valide und bereit für die Sandbox.")
    assert ic.format_report(f[1:]).rstrip().endswith("Akzeptanztests sind valide und bereit für die Sandbox.")


def test_main_exit_codes(tmp_path, repo, capsys):
    gut, schlecht = tmp_path / "gut.md", tmp_path / "schlecht.md"
    gut.write_text(issue(), encoding="utf-8")
    schlecht.write_text(issue(block="def helfer():\n    pass\n"), encoding="utf-8")
    assert ic.main([str(gut), "--repo", str(repo)]) == 0
    assert "bereit für die Sandbox" in capsys.readouterr().out
    assert ic.main([str(schlecht), "--repo", str(repo), "--no-run"]) == 1
    assert "kein-test" in capsys.readouterr().out
