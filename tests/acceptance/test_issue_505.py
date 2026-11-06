"""Akzeptanztests: Upload steht als eigener Menüpunkt in der Hauptnavigation."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
BASE = (ROOT / "sc_digger" / "web" / "templates" / "base.html").read_text(encoding="utf-8")
PASSWORD = "geheim-nav"
AUTH = ("sc", PASSWORD)


def _nav(html: str) -> str:
    start = html.index('<nav class="nav-tabs">')
    return html[start:html.index("</nav>", start)]


def _vor_mehr(nav: str) -> str:
    """Der Teil der Navigation vor dem Ausklappmenü „Mehr“: die Haupteinträge."""
    return nav[:nav.index('class="nav-more"')]


def _mehr_liste(nav: str) -> str:
    start = nav.index('class="nav-more-list"')
    return nav[start:nav.index("</details>", start)]


def test_upload_steht_in_der_hauptnavigation():
    nav = _nav(BASE)
    assert 'href="/intake"' in _vor_mehr(nav), "Upload steht nicht in der Hauptnavigation"


def test_upload_steht_nicht_mehr_im_mehr_menue():
    assert 'href="/intake"' not in _mehr_liste(_nav(BASE))


def test_hauptnavigation_behaelt_ihre_eintraege_in_der_reihenfolge():
    haupt = re.findall(r'href="([^"]+)"', _vor_mehr(_nav(BASE)))
    assert haupt == ["/", "/tracks", "/digest", "/intake", "/mix", "/kaufliste"], haupt


def test_mehr_menue_behaelt_duplikate_quarantaene_konfiguration():
    mehr = _mehr_liste(_nav(BASE))
    for pfad in ("/duplicates", "/rejected", "/config"):
        assert f'href="{pfad}"' in mehr, f"{pfad} fehlt im Mehr-Menü"


def test_hauptnavigation_bleibt_im_gerenderten_html(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    html = TestClient(create_app(cfg, password=PASSWORD)).get("/tracks", auth=AUTH).text
    assert 'href="/intake"' in _vor_mehr(_nav(html))