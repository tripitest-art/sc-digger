"""Akzeptanztests: Mix-Seite – Finder zuerst, Export am berechneten Set, Scanner-Leerzustand."""
import re
from pathlib import Path

from tests.test_web_tracks_ui import PASSWORD, client_for

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "sc_digger" / "web" / "templates" / "mix.html").read_text(encoding="utf-8")


def _seite(tmp_path):
    antwort = client_for(tmp_path).get("/mix?key=8A&bpm=150", auth=("sc", PASSWORD))
    assert antwort.status_code == 200
    return antwort.text


def _funktion(name):
    start = TEMPLATE.index(f"async function {name}(")
    ende = TEMPLATE.find("\n  }\n", start)
    return TEMPLATE[start:ende]


def test_mix_finder_steht_vor_dem_set_builder(tmp_path):
    html = _seite(tmp_path)
    finder = html.index('id="mix-finder"')
    builder = html.index('id="set-builder"')
    scanner = html.index('id="url-input"')
    assert finder < builder < scanner


def test_export_knoepfe_sind_anfangs_gesperrt(tmp_path):
    html = _seite(tmp_path)
    download = re.search(r'<a id="sb-download"[^>]*>', html).group(0)
    assert "href=" not in download
    assert 'aria-disabled="true"' in download
    xml = re.search(r'<button[^>]*id="sb-export-xml"[^>]*>', html).group(0)
    assert re.search(r"\sdisabled(\s|>|=)", xml)


def test_set_builder_merkt_sich_die_parameter():
    assert re.search(r"^\s*let lastSetParams = null;", TEMPLATE, re.M)
    teil = _funktion("buildSet")
    assert "lastSetParams = params" in teil
    assert "lastSetParams = null" in teil


def test_xml_export_nutzt_das_berechnete_set():
    teil = _funktion("exportSetXml")
    assert "new FormData(" not in teil
    assert "lastSetParams" in teil
    assert "Erst ein Set berechnen." in teil


def test_scanner_zeigt_leerzustand(tmp_path):
    html = _seite(tmp_path)
    tbody = re.search(r'<tbody id="scan-rows">(.*?)</tbody>', html, re.S).group(1)
    assert "Noch kein Link geprüft." in tbody
    assert 'colspan="6"' in tbody
