"""Akzeptanztests: Spalten in der Tracks- und Digest-Tabelle ein- und ausblenden."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "sc_digger" / "web" / "templates"

# Spalten, die sich ausblenden lassen; Aktionen (Anhören, Übernahme, Feedback, Links) nicht.
SPALTEN = {
    "tracks": ("artist", "title", "format", "bpm", "key", "quality", "status", "source"),
    "digest": ("date", "artist", "title", "download_kind", "status", "feedback"),
}


def _t(name: str) -> str:
    return (TEMPLATES / f"{name}.html").read_text(encoding="utf-8")


def test_jeder_spaltenkopf_hat_einen_slug():
    for seite, slugs in SPALTEN.items():
        text = _t(seite)
        gefunden = re.findall(r'<th[^>]*data-column="([^"]+)"', text)
        assert gefunden, f"{seite}.html: kein th mit data-column"
        assert list(gefunden) == list(slugs), f"{seite}.html: Spalten {gefunden} statt {list(slugs)}"


def test_jede_datenzelle_traegt_den_slug_ihrer_spalte():
    """Ohne data-column in den Zellen kann ausgeblendet werden."""
    for seite, slugs in SPALTEN.items():
        text = _t(seite)
        zellen = re.findall(r'<td[^>]*data-column="([^"]+)"', text)
        assert zellen, f"{seite}.html: keine Zelle mit data-column"
        assert set(zellen) == set(slugs), f"{seite}.html: Zell-Slugs {sorted(set(zellen))}"


def test_auswahl_hat_fuer_jede_spalte_ein_kaestchen():
    for seite, slugs in SPALTEN.items():
        text = _t(seite)
        kaestchen = re.findall(r'<input[^>]*type="checkbox"[^>]*data-column="([^"]+)"', text)
        assert list(kaestchen) == list(slugs), f"{seite}.html: Kaestchen {kaestchen}"


def test_auswahl_ist_ohne_skript_erreichbar_und_zuruecksetzbar():
    for seite in SPALTEN:
        text = _t(seite)
        assert re.search(r'<details[^>]*class="[^"]*spalten-wahl', text), f"{seite}.html: kein Auswahlfeld"
        assert 'data-action="alle-spalten"' in text, f"{seite}.html: kein Zuruecksetzen"
        # Ohne JavaScript ist die Tabelle vollstaendig: alle Spaltenkoepfe stehen im HTML.
        assert len(re.findall(r"<th\b", text)) >= len(SPALTEN[seite]) + 1


def test_einstellung_bleibt_im_browser_erhalten():
    for seite, slugs in SPALTEN.items():
        text = _t(seite)
        assert "localStorage" in text, f"{seite}.html: keine Speicherung"
        assert f"sc-digger.columns.{seite}" in text, f"{seite}.html: kein eigener Speicherplatz"
        assert "data-hidden-columns" in text, f"{seite}.html: Ausblenden nicht am Tabellenelement"
