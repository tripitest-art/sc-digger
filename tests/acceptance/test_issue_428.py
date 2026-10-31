"""Akzeptanztests: Dashboard passt sich dem Viewport an (Tabellen, Navigation, Filter)."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "sc_digger" / "web" / "templates"
PASSWORD = "geheim-viewport"
AUTH = ("sc", PASSWORD)


def _template(name: str) -> str:
    path = TEMPLATES / name
    assert path.is_file(), f"{name} fehlt"
    return path.read_text(encoding="utf-8")


def _css() -> str:
    """Der gesamte Stil, der im Browser ankommt: inline in base.html plus eingebundene Dateien."""
    text = _template("base.html")
    for name in re.findall(r'href="[^"]*static/([^"]+\.css)"', text):
        path = ROOT / "sc_digger" / "web" / "static" / name
        if path.is_file():
            text += "\n" + path.read_text(encoding="utf-8")
    return text


def client_for(tmp_path: Path) -> TestClient:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.upsert_track(TrackRecord(
            path="/music/Schranz/1/a.wav", mtime=1.0, size=1000,
            artist="Svetec", title="Acid Kick", format="wav", bpm=150.0,
            key_camelot="8A", quality_status=QualityStatus.OK, status=TrackStatus.ARCHIVE,
        ))
    return TestClient(create_app(cfg, password=PASSWORD))


# --- Grundlage -------------------------------------------------------------------


def test_viewport_ist_gesetzt():
    assert re.search(r'<meta[^>]+name="viewport"[^>]+width=device-width', _template("base.html"))


def test_es_gibt_breakpoints_fuer_schmale_viewports():
    css = _css()
    breiten = [int(w) for w in re.findall(r"@media\s*\(\s*max-width:\s*(\d+)px", css)]
    assert breiten, "keine Media Query für schmale Viewports"
    assert any(w >= 500 for w in breiten), breiten
    assert any(w <= 600 for w in breiten), breiten


# --- Tabellen --------------------------------------------------------------------


def test_tabellen_liegen_in_einem_scrollbereich_mit_overflow():
    css = _css()
    assert re.search(r"\.table-scroll\s*\{[^}]*overflow-x:\s*auto", css), \
        ".table-scroll ohne overflow-x: auto"
    for name in ("tracks.html", "digest.html"):
        text = _template(name)
        assert re.search(r'class="table-scroll"', text), f"{name} wickelt die Tabelle nicht ein"
        assert text.count('class="table-scroll"') >= text.count("<table"), name


def test_tabellen_haben_keine_feste_mindestbreite():
    css = _css()
    for block in re.findall(r"table[^{]*\{([^}]*)\}", css):
        assert "min-width" not in block, block


def test_tabellen_brechen_in_schmalen_viewports_um():
    """Unter dem Breakpoint darf der Zelleninhalt umbrechen statt die Seite zu sprengen."""
    css = _css()
    schmal = re.findall(r"@media\s*\(\s*max-width:\s*\d+px\s*\)\s*\{(.*?)\n\}", css, re.S)
    assert schmal, "keine @media-Blöcke gefunden"
    zusammengefasst = "\n".join(schmal)
    assert "td" in zusammengefasst or "table" in zusammengefasst, zusammengefasst[:200]


def test_die_seiten_liefern_den_scrollbereich_aus(tmp_path):
    client = client_for(tmp_path)
    for pfad in ("/tracks", "/digest"):
        antwort = client.get(pfad, auth=AUTH)
        assert antwort.status_code == 200, pfad
        assert 'class="table-scroll"' in antwort.text, pfad


# --- Navigation und Filter -------------------------------------------------------


def test_navigation_bricht_in_schmalen_viewports_um():
    css = _css()
    block = re.search(r"\.nav-tabs\s*\{([^}]*)\}", css)
    assert block, ".nav-tabs fehlt im Stil"
    assert "flex-wrap" in block.group(1), block.group(1)
    schmal = re.findall(r"@media\s*\(\s*max-width:\s*\d+px\s*\)\s*\{(.*?)\n\}", css, re.S)
    assert any("nav-tabs" in block or "nav-tab" in block for block in schmal), \
        "Navigation wird unter dem Breakpoint nicht angepasst"


def test_filterformular_wird_unter_dem_breakpoint_einspaltig():
    css = _css()
    assert re.search(r"\.filter-grid\s*\{[^}]*grid-template-columns", css), \
        ".filter-grid nutzt kein Grid"
    schmal = re.findall(r"@media\s*\(\s*max-width:\s*\d+px\s*\)\s*\{(.*?)\n\}", css, re.S)
    zusammen = "\n".join(schmal)
    assert re.search(r"\.filter-grid[^{]*\{[^}]*grid-template-columns:\s*1fr", zusammen), \
        "Filterraster wird unter dem Breakpoint nicht einspaltig"


def test_bilder_und_medien_bleiben_im_rahmen():
    css = _css()
    assert re.search(r"max-width:\s*100%", css), "keine Regel max-width: 100% für Medien"
