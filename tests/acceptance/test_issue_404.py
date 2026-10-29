"""Akzeptanztest: die Altlast ``_eval/`` ist entfernt, README und CHANGELOG sind mitgezogen.

Sinn: Das Verzeichnis wird von keinem Produktionscode benutzt, steht aber in der README
als Werkzeugkasten mit Windows-Befehlen und macht Textsuche und CodeQL unscharf.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SELBST = Path(__file__).resolve()


def test_eval_verzeichnis_ist_weg() -> None:
    assert not (ROOT / "_eval").exists(), "_eval/ liegt noch im Arbeitsbaum"


def test_readme_nennt_keine_eval_pfade_mehr() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "_eval" not in text, "README nennt weiterhin _eval-Pfade oder -Befehle"


def test_kein_code_importiert_aus_eval() -> None:
    treffer = []
    for pfad in [*(ROOT / "sc_digger").rglob("*.py"), *(ROOT / "tests").rglob("*.py")]:
        if pfad.resolve() == SELBST:
            continue
        text = pfad.read_text(encoding="utf-8")
        if "import _eval" in text or "from _eval" in text:
            treffer.append(pfad.relative_to(ROOT).as_posix())
    assert treffer == [], f"Importe aus _eval gefunden: {treffer}"


def test_changelog_nennt_die_entfernung() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "_eval" in text, "CHANGELOG hat keine Zeile zur Entfernung von _eval/"
