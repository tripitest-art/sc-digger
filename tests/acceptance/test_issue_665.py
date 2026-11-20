"""Akzeptanztests: Doku-Nachzug für Arbeitsbereiche, Heute-Karte und Abhaken (Issue #665)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FUNKTIONEN = (ROOT / "docs" / "funktionen.md").read_text(encoding="utf-8")


def test_arbeitsbereiche_sind_dokumentiert():
    assert "Arbeitsbereiche" in FUNKTIONEN
    for bereich in ("Start", "Entdecken", "Eingang", "Bibliothek", "Auflegen"):
        assert bereich in FUNKTIONEN, f"Arbeitsbereich {bereich} fehlt"


def test_heute_karte_ist_dokumentiert():
    assert "Heute zu tun" in FUNKTIONEN
    assert "/heute" in FUNKTIONEN
    assert "Gates" in FUNKTIONEN


def test_abhaken_ist_dokumentiert():
    assert "erledigt" in FUNKTIONEN
    assert "wieder öffnen" in FUNKTIONEN
    assert "#649" in FUNKTIONEN or "#642" in FUNKTIONEN
