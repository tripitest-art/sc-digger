"""Akzeptanztests: Begriffe des Glossars gelten auf allen Seiten (Issue <N>)."""
from __future__ import annotations

from pathlib import Path

from sc_digger.web.glossary import GLOSSAR, find_forbidden, forbidden_words

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "sc_digger" / "web" / "templates"


def test_eingang_ist_der_bereich_und_der_ordner_heisst_upload_ordner():
    assert GLOSSAR["Inbox"][1] == ()
    assert "Eingang" not in GLOSSAR
    assert GLOSSAR["Upload-Ordner"] == ("Ordner _eingang für manuell geladene Dateien (Upload, Gates).", ("Intake",))
    assert "Eingang" not in forbidden_words()
    assert forbidden_words()["Künstler"] == "Artist"


def test_find_forbidden_findet_sichtbare_woerter():
    html = ('<h1>Abgelehnte Tracks</h1><p>3 Künstler · Erfolgreiche Runs</p>'
            '<p>Master-Archiv</p><p>Kein Treffer: Runde, Archivierung</p>')
    assert find_forbidden(html) == ["Abgelehnte", "Künstler", "Runs", "Archiv"]


def test_find_forbidden_ignoriert_code_attribute_und_skripte():
    html = ('<a href="/intake" title="Künstler">Upload</a><code>archive</code>'
            '{{ "Künstler" }}{% if x == "Run" %}{% endif %}{# Abgelehnt #}<!-- Intake -->'
            '<script>const t = "abgelehnt";</script><style>.run{}</style>')
    assert find_forbidden(html) == []


def test_alle_templates_halten_das_glossar_ein():
    befunde = {}
    for path in sorted(TEMPLATES.glob("*.html")):
        if path.name == "glossar.html":
            continue
        treffer = find_forbidden(path.read_text(encoding="utf-8"))
        if treffer:
            befunde[path.name] = treffer
    assert befunde == {}


def test_englische_oberflaechenwoerter_sind_ersetzt():
    alles = "\n".join(p.read_text(encoding="utf-8") for p in TEMPLATES.glob("*.html"))
    for alt in ("Harmonic Mix Finder", "DJ Ready", "Curation &amp; DJ Pipeline", "👎 Aussortiert"):
        assert alt not in alles, alt
    assert "🧭 Passende Tracks" in (TEMPLATES / "mix.html").read_text(encoding="utf-8")
    assert "Passt nicht" in (TEMPLATES / "kaufliste.html").read_text(encoding="utf-8")


def test_status_plaketten_kommen_aus_dem_makro():
    for name in ("tracks.html", "duplicates.html"):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "status_badge(" in text, name
        for roh in ('badge-purple">archive<', 'badge-ok">archive<', 'badge-err">Abgelehnt<', 'badge-err">rejected<'):
            assert roh not in text, (name, roh)
