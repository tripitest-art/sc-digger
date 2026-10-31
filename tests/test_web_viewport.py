"""Tests für Viewport-Anpassung, responsive Breakpoints und Tabellen-Scrollbereiche (#428)."""
from __future__ import annotations

import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "sc_digger" / "web" / "templates"


def _read_template(name: str) -> str:
    path = TEMPLATES_DIR / name
    assert path.is_file(), f"Template {name} existiert nicht"
    return path.read_text(encoding="utf-8")


def test_breakpoint_reihenfolge_breit_vor_schmal():
    """Breiter Breakpoint (Laptop) muss vor dem schmalen Breakpoint (Telefon) im CSS stehen."""
    css = _read_template("base.html")
    matches = [int(w) for w in re.findall(r"@media\s*\(\s*max-width:\s*(\d+)px", css)]
    assert len(matches) >= 2, f"Erwartet mindestens 2 Breakpoints, gefunden: {matches}"

    breiter = next(w for w in matches if w >= 700)
    schmaler = next(w for w in matches if w <= 600)

    idx_breit = matches.index(breiter)
    idx_schmal = matches.index(schmaler)
    assert idx_breit < idx_schmal, f"Breiter Breakpoint ({breiter}px) muss vor schmalem ({schmaler}px) stehen"


def test_alle_tabellen_in_tracks_und_digest_geklammert():
    """Jede Tabelle in tracks.html und digest.html muss in einem .table-scroll Container liegen."""
    for name in ("tracks.html", "digest.html"):
        content = _read_template(name)
        tables = re.findall(r"<table\b", content)
        scroll_divs = re.findall(r'class="table-scroll"', content)
        assert len(tables) > 0, f"{name} enthält keine Tabelle"
        assert len(scroll_divs) >= len(tables), (
            f"{name}: {len(tables)} Tabellen gefunden, aber nur {len(scroll_divs)} .table-scroll Container"
        )
        assert re.search(r'<div class="table-scroll">\s*<table', content), (
            f"{name}: Tabelle liegt nicht direkt im .table-scroll div"
        )


def test_table_scroll_hat_touch_scrolling():
    """Touch-Geräte (z. B. iOS Safari) benötigen momentum-based touch scrolling."""
    css = _read_template("base.html")
    block = re.search(r"\.table-scroll\s*\{([^}]*)\}", css)
    assert block is not None, ".table-scroll fehlt in base.html"
    assert "overflow-x: auto" in block.group(1)
    assert "-webkit-overflow-scrolling: touch" in block.group(1)


def test_kein_important_in_neuen_viewport_regeln():
    """Saubere Spezifität ohne !important-Hacks (Vorgabe aus Issue #428)."""
    css = _read_template("base.html")
    # Suche ab dem Viewport-Abschnitt bis </style>
    start = css.find("/* Viewport-Anpassung")
    end = css.find("</style>", start)
    viewport_css = css[start:end]
    assert "!important" not in viewport_css, "Es darf kein !important verwendet werden"
