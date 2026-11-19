"""Akzeptanztests: feste Status-Plaketten und Glossar."""
from pathlib import Path

from sc_digger.web.common import _env
from sc_digger.web.glossary import GLOSSAR, STATUS_BADGES, badge_for

ROOT = Path(__file__).resolve().parents[2]


def _makro(key):
    vorlage = _env.from_string('{% from "_components.html" import status_badge %}{{ status_badge(k) }}')
    return vorlage.render(k=key).strip()


def test_feste_zuordnung():
    assert STATUS_BADGES == {
        "archive": ("Sammlung", "badge-ok"),
        "adopted": ("übernommen", "badge-ok"),
        "inbox": ("Inbox", "badge-info"),
        "rejected": ("Quarantäne", "badge-err"),
        "gate": ("Gate", "badge-warn"),
        "store": ("Store", "badge-purple"),
        "open": ("offen", "badge-muted"),
    }


def test_badge_for():
    assert badge_for("inbox") == ("Inbox", "badge-info")
    assert badge_for("ARCHIVE") == ("Sammlung", "badge-ok")
    assert badge_for("unbekannt") == ("unbekannt", "badge-muted")
    assert badge_for(None) == ("—", "badge-muted")
    assert badge_for("") == ("—", "badge-muted")


def test_makro_rendert_die_plakette():
    assert _makro("inbox") == '<span class="badge badge-info">Inbox</span>'
    assert _makro("rejected") == '<span class="badge badge-err">Quarantäne</span>'
    assert _makro("xyz") == '<span class="badge badge-muted">xyz</span>'
    assert _makro("<b>") == '<span class="badge badge-muted">&lt;b&gt;</span>'


def test_glossar_seite_entspricht_der_tabelle():
    text = (ROOT / "docs" / "glossar.md").read_text(encoding="utf-8")
    assert text.startswith("# Glossar\n")
    positionen = []
    for begriff, (erklaerung, nicht) in GLOSSAR.items():
        zeile = f"- **{begriff}**: {erklaerung}"
        if nicht:
            zeile += " Nicht: " + ", ".join(nicht) + "."
        assert zeile in text, begriff
        positionen.append(text.index(zeile))
    assert positionen == sorted(positionen)


def test_jeder_plakettenbegriff_steht_im_glossar():
    for wort, _klasse in STATUS_BADGES.values():
        if wort != "offen":
            assert wort in GLOSSAR, wort


def test_readme_verlinkt_das_glossar():
    assert "docs/glossar.md" in (ROOT / "README.md").read_text(encoding="utf-8")
