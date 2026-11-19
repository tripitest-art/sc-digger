"""Akzeptanztests: Ladezustand beim Nachladen der Tabellen, Fehlerfarbe im Ähnlich-Panel."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "sc_digger" / "web" / "templates"


def _quelle(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def _funktion(text, name):
    start = text.index(f"async function {name}(")
    ende = text.find("\n  }\n", start)
    return text[start:ende]


def test_nachladen_markiert_den_bereich_vor_dem_abruf():
    for name in ("tracks.html", "digest.html"):
        teil = _funktion(_quelle(name), "fetchTableRegion")
        markiert = teil.index("setAttribute('aria-busy', 'true')")
        abruf = teil.index("await fetch(")
        assert markiert < abruf, name


def test_markierung_wird_im_finally_entfernt():
    for name in ("tracks.html", "digest.html"):
        teil = _funktion(_quelle(name), "fetchTableRegion")
        assert "finally" in teil, name
        assert "removeAttribute('aria-busy')" in teil[teil.index("finally"):], name


def test_ladezustand_ist_sichtbar_und_sperrt_klicks():
    for name in ("tracks.html", "digest.html"):
        regel = re.search(r'#table-region\[aria-busy="true"\]\s*\{([^}]*)\}', _quelle(name))
        assert regel, name
        assert "opacity" in regel.group(1), name
        assert "pointer-events: none" in regel.group(1), name


def test_fehlerfarbe_im_aehnlich_panel_ist_definiert():
    regel = re.search(r"\.similar-panel-error\s*\{([^}]*)\}", _quelle("tracks.html"))
    assert regel
    assert "var(--accent-err)" in regel.group(1)
    assert "--accent-err:" in _quelle("base.html")
    for datei in TEMPLATES.glob("*.html"):
        assert "var(--err)" not in datei.read_text(encoding="utf-8"), datei.name
