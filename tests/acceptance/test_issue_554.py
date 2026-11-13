"""Akzeptanztests: Filtern, Sortieren und Blättern ohne vollständiges Neuladen."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "sc_digger" / "web" / "templates"
PASSWORD = "geheim-partial"
AUTH = ("sc", PASSWORD)
PARTIAL = {"X-Sc-Digger-Partial": "1"}


def _client(tmp_path: Path) -> TestClient:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return TestClient(create_app(cfg, password=PASSWORD))


def _t(name: str) -> str:
    return (TEMPLATES / f"{name}.html").read_text(encoding="utf-8")


def test_teilantwort_enthaelt_nur_den_ergebnisbereich(tmp_path):
    client = _client(tmp_path)
    for url in ("/tracks", "/digest"):
        teil = client.get(url, headers=PARTIAL, auth=AUTH).text
        assert "<html" not in teil.lower(), f"{url}: Teilantwort enthaelt das Seitengeruest"
        assert '<nav class="nav-tabs">' not in teil, f"{url}: Teilantwort enthaelt die Navigation"
        assert 'id="table-region"' in teil, f"{url}: Teilantwort ohne table-region"
        assert 'name="limit"' in teil, f"{url}: Teilantwort ohne Zeilenwahl"


def test_vollstaendige_antwort_enthaelt_bereich_und_geruest(tmp_path):
    client = _client(tmp_path)
    for url in ("/tracks", "/digest"):
        voll = client.get(url, auth=AUTH).text
        assert "<html" in voll.lower(), f"{url}: kein Seitengeruest"
        assert '<nav class="nav-tabs">' in voll, f"{url}: keine Navigation"
        assert 'id="table-region"' in voll, f"{url}: kein table-region"


def test_bereich_umschliesst_tabelle_und_blaettern():
    for seite in ("tracks", "digest"):
        text = _t(seite)
        start = text.index('id="table-region"')
        ende = text.index("{% endblock %}", start)
        bereich = text[start:ende]
        assert 'class="table-scroll"' in bereich, f"{seite}.html: Tabelle liegt ausserhalb"
        assert 'class="btn-page' in bereich, f"{seite}.html: Blaettern liegt ausserhalb"


def test_skript_tauscht_den_bereich_ohne_neuladen():
    for seite in ("tracks", "digest"):
        text = _t(seite)
        assert "X-Sc-Digger-Partial" in text, f"{seite}.html: Kopfzeile fuer Teilantwort fehlt"
        assert "history.pushState" in text, f"{seite}.html: Adresse wird nicht nachgezogen"
        assert "'popstate'" in text or '"popstate"' in text, f"{seite}.html: Zurueck-Knopf nicht bedacht"
        assert "table-region" in text, f"{seite}.html: Zielbereich wird nicht ersetzt"


def test_ohne_skript_bleibt_das_formular_gueltig():
    """Progressive Erweiterung: ohne JavaScript filtert das GET-Formular wie bisher."""
    for seite, ziel in (("tracks", "/tracks"), ("digest", "/digest")):
        text = _t(seite)
        form = re.search(r"<form\b[^>]*>", text).group(0)
        assert re.search(r'method="get"', form, re.I), f"{seite}.html: Formular ohne GET"
        assert f'action="{ziel}"' in form, f"{seite}.html: Formular ohne Ziel"
        assert re.search(r'<a href="[^"]*\?[^"]*sort=', text), f"{seite}.html: Sortier-Link fehlt"
