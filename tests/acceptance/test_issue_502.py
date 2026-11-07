"""Akzeptanztests: Tracks- und Digest-Tabelle lesbar auf schmalen Bildschirmen (Karten statt Quetschung)."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "sc_digger" / "web" / "templates"
PASSWORD = "geheim-tabelle"
AUTH = ("sc", PASSWORD)

# Spalten, die in der Kartenansicht ein Label brauchen (Rest ist Aktion oder Bild)
KARTEN_SPALTEN = {
    "tracks": ("Artist", "Titel", "Format", "BPM", "Key", "Qualität", "Status", "Quelle"),
    "digest": ("Datum", "Artist", "Titel", "Download-Art", "Status", "Feedback"),
}


def _client(tmp_path: Path) -> TestClient:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return TestClient(create_app(cfg, password=PASSWORD))


def _template(name: str) -> str:
    return (TEMPLATES / f"{name}.html").read_text(encoding="utf-8")


# --- Kartenansicht: jede Zelle kennt ihr Label ------------------------------------


def test_jede_datenzelle_traegt_ein_label():
    """Ohne `data-label` kann die Kartenansicht nicht zeigen, welcher Wert was ist."""
    for seite, spalten in KARTEN_SPALTEN.items():
        text = _template(seite)
        zellen = re.findall(r"<td\b[^>]*>", text)
        assert zellen, f"{seite}.html hat keine <td>"
        ohne = [z for z in zellen if "data-label=" not in z]
        assert not ohne, f"{seite}.html: {len(ohne)} Zellen ohne data-label, z. B. {ohne[:2]}"
        for spalte in spalten:
            assert f'data-label="{spalte}"' in text, f'{seite}.html: data-label="{spalte}" fehlt'


def test_labels_entsprechen_den_spaltenkoepfen():
    """Tippfehler im Label fallen auf, weil es den Spaltenkopf sonst nicht gibt."""
    for seite in KARTEN_SPALTEN:
        text = _template(seite)
        koepfe = set(re.findall(r"<th\b[^>]*>\s*(?:<a\b[^>]*>)?([A-ZÄÖÜ][^<{]{1,20})", text))
        koepfe = {k.strip() for k in koepfe}
        labels = set(re.findall(r'data-label="([^"]+)"', text))
        assert labels, f"{seite}.html: keine data-label gefunden"
        assert labels <= koepfe, f"{seite}.html: Label ohne Spaltenkopf: {sorted(labels - koepfe)}"


# --- Umschaltung per CSS ----------------------------------------------------------


def test_css_schaltet_unter_900px_auf_karten():
    """Unter 900 px wird aus der Tabelle eine Liste aus Blöcken mit Label davor."""
    base = _template("base")
    assert re.search(r"@media\s*\(max-width:\s*900px\)", base), "Media-Query für 900 px fehlt"
    block = base[base.index("@media (max-width: 900px)") if "@media (max-width: 900px)" in base else 0:]
    assert "attr(data-label)" in base, "Label wird nicht per attr(data-label) angezeigt"
    assert re.search(r"thead\s*\{[^}]*display\s*:\s*none", base), "Spaltenköpfe werden nicht ausgeblendet"
    assert block, "900-px-Block nicht gefunden"


def test_tabellen_bleiben_bei_breite_scrollbar():
    """Auf breiten Bildschirmen bleibt die Tabelle eine Tabelle, mit Scrollbehälter."""
    base = _template("base")
    assert ".table-scroll" in base
    for seite in KARTEN_SPALTEN:
        assert 'class="table-scroll"' in _template(seite), f"{seite}.html nutzt .table-scroll nicht"


# --- Bestehende Bedienung bleibt --------------------------------------------------


def test_filter_sortierung_und_zeilenmenge_bleiben(tmp_path):
    client = _client(tmp_path)
    for url in ("/tracks", "/digest"):
        html = client.get(url, auth=AUTH).text
        assert 'name="limit"' in html, f"{url}: Zeilenwahl fehlt"
        assert re.search(r'href="[^"]*sort=[^"]*"', html), f"{url}: Sortier-Link fehlt"
        assert re.search(r'<form\b[^>]*method="get"', html, re.I), f"{url}: Filterformular fehlt"