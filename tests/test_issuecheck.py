"""Tests für sc_digger.issuecheck (Planer-Preflight, ohne Netzwerk)."""
import io
import re
import sys
from pathlib import Path

import pytest

from sc_digger import issuecheck as ic

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / ".github" / "ISSUE_TEMPLATE" / "worker-task.yml"

GOOD = "from sc_digger.neu import f\n\n\ndef test_f():\n    assert f() == 1\n"


def _issue(block=GOOD, merge="automatisch nach Review", fence="python"):
    """Ein Issue-Text in der Form, die das Worker-Formular rendert."""
    bodies = {
        "Ziel": "Etwas bauen.",
        "Betroffene Dateien": "- `sc_digger/neu.py`",
        "Schnittstellen": "```python\ndef f() -> int: ...\n```",
        "Nicht Teil dieser Aufgabe": "- nichts anderes",
        "Akzeptanztests": f"```{fence}\n{block}```",
        "Fertig, wenn": "- [ ] Akzeptanztests grün",
        "Berührt sc_digger/main.py": "nein",
        "Merge-Modus": merge,
        "Kontext": "Planer: Claude.",
    }
    return "\n\n".join(f"### {name}\n\n{bodies[name]}" for name in ic.REQUIRED_HEADINGS) + "\n"


def _template_labels() -> list[str]:
    text = TEMPLATE.read_text(encoding="utf-8")
    return [m.group(1).strip() for m in re.finditer(r"^\s+label:\s*(.+)$", text, re.M)]


def _codes(findings):
    return sorted(f.code for f in findings)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "sc_digger").mkdir(parents=True)
    (r / "tests").mkdir()
    (r / "sc_digger" / "__init__.py").write_text("")
    (r / "sc_digger" / "vorhanden.py").write_text("def f():\n    return 1\n")
    (r / "tests" / "__init__.py").write_text("")
    return r


# ---------------- Issue-Muster aus dem Formular ----------------
def test_formular_ueberschriften_sind_die_pflichtueberschriften():
    assert _template_labels() == list(ic.REQUIRED_HEADINGS)


def test_issue_muster_aus_dem_formular_besteht_check_static():
    assert ic.check_static(_issue()) == []


def test_merge_modus_dropdown_texte_aus_dem_formular_sind_gueltig():
    for wert in ("automatisch nach Review", "manuell durch Stephan (Betrieb, Dateien, Zugangsdaten, Downloads)"):
        assert ic.check_static(_issue(merge=wert)) == []


# ---------------- Zusatzprüfungen ----------------
def test_leerer_block_ist_kein_block():
    assert _codes(ic.check_static(_issue(block=""))) == ["kein-block"]


def test_tilde_zaun_und_crlf_werden_normalisiert():
    doc = "### Akzeptanztests\r\n\r\n~~~~python\r\n" + GOOD.replace("\n", "\r\n") + "\r\n~~~~\r\n"
    assert ic.extract_acceptance_block(doc) == GOOD.strip("\n")


def test_doppelter_verbotener_import_ist_ein_befund():
    block = "import requests\nimport requests\n\n\ndef test_x():\n    assert True\n"
    found = ic.check_static(_issue(block=block))
    assert _codes(found).count("verbotener-import") == 1


def test_skip_und_uhrzeit_in_zeichenketten_sind_kein_befund():
    # Ein Prüfer, der diese Namen als Daten im Testcode stehen hat, überspringt nichts und
    # wartet auf nichts. Deshalb zählen nur echte Attribut-Zugriffe, keine Zeichenketten.
    block = (
        "import pytest\n\n\n"
        "def test_detektor():\n"
        "    text = \"pytest.importorskip('numpy') time.sleep(1) random.random()\"\n"
        "    assert \"time.sleep\" in text\n"
    )
    assert ic.check_static(_issue(block=block)) == []


def test_skip_wird_auch_ausserhalb_von_pytest_erkannt():
    block = "import unittest\n\n\n@unittest.skip('später')\ndef test_x():\n    assert True\n"
    assert _codes(ic.check_static(_issue(block=block))) == ["skip-xfail"]


def test_uhrzeit_und_zufall_als_zugriff_sind_befunde():
    kopf = ("import datetime\nimport time\nimport random\n"
            "from datetime import datetime as dt\n\n\n")
    for zeile in ("time.sleep(1)", "time.time()", "dt.now()",
                  "datetime.datetime.utcnow()", "random.randint(1, 2)"):
        block = kopf + f"def test_x():\n    value = {zeile}\n    assert value is not None\n"
        assert "verbotener-aufruf" in _codes(ic.check_static(_issue(block=block))), zeile


def test_schnittstellen_block_gilt_nicht_als_akzeptanztest():
    # Der ```python-Block unter „Schnittstellen“ darf nicht als Akzeptanztest gelesen werden.
    doc = _issue(block="", fence="python")
    assert ic.extract_acceptance_block(doc) is None


# ---------------- main: Datei, stdin, Rückgabewerte ----------------
def test_main_mit_datei_ist_gruen(repo, tmp_path, capsys):
    p = tmp_path / "issue.md"
    p.write_text(_issue(), encoding="utf-8")
    assert ic.main([str(p), "--repo", str(repo)]) == 0
    assert "bereit für die Sandbox" in capsys.readouterr().out


def test_main_liest_stdin(repo, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(_issue()))
    assert ic.main(["-", "--repo", str(repo)]) == 0
    assert "bereit für die Sandbox" in capsys.readouterr().out


def test_main_exitcode_eins_bei_fehler(tmp_path, capsys):
    p = tmp_path / "schlecht.md"
    p.write_text(_issue(block="def helfer():\n    pass\n"), encoding="utf-8")
    assert ic.main([str(p), "--no-run"]) == 1
    assert "kein-test" in capsys.readouterr().out


def test_main_exitcode_null_bei_blosser_warnung(tmp_path, capsys):
    p = tmp_path / "warnung.md"
    p.write_text(_issue(block="import mutagen\n\n\ndef test_x():\n    assert mutagen\n"), encoding="utf-8")
    assert ic.main([str(p), "--no-run"]) == 0
    out = capsys.readouterr().out
    assert "fremder-import" in out and "bereit für die Sandbox" in out


def test_main_meldet_roten_test_nicht_als_fehler(repo, tmp_path, capsys):
    p = tmp_path / "issue.md"
    p.write_text(_issue(), encoding="utf-8")
    assert ic.main([str(p), "--repo", str(repo)]) == 0
    assert "Nicht einstellen" not in capsys.readouterr().out
