"""Tests für die überarbeitete Navigation mit „Mehr“-Menü (Issue #484)."""
import re
from pathlib import Path

from tests.test_web_tracks_ui import PASSWORD, client_for


def _get(tmp_path: Path, path: str):
    return client_for(tmp_path).get(path, auth=("sc", PASSWORD))


def _nav(html: str) -> str:
    match = re.search(r'<nav class="nav-tabs">(.*?)</nav>', html, re.S)
    assert match, "nav-tabs nicht gefunden"
    return match.group(1)


def test_nav_struktur_und_reihenfolge(tmp_path: Path):
    """Prüft Reihenfolge der Haupteinträge und dass Mehr am Ende steht."""
    html = _get(tmp_path, "/").text
    nav = _nav(html)
    assert '<details class="nav-more">' in nav

    haupt, mehr = nav.split('<details class="nav-more">')
    # Haupteinträge exakt in Reihenfolge
    expected_order = [
        ('href="/"', "Status"),
        ('href="/tracks"', "Tracks"),
        ('href="/digest"', "Digest"),
        ('href="/mix"', "Mix"),
        ('href="/kaufliste"', "Kaufliste"),
    ]
    pos = 0
    for href, label in expected_order:
        idx = haupt.find(href)
        assert idx != -1, f"{href} fehlt im Hauptmenü"
        assert idx >= pos, f"{href} steht nicht in erwarteter Reihenfolge"
        pos = idx
        assert label in haupt

    # Keine Emojis in den Menütexten von Kaufliste und Quarantäne
    assert "🛒" not in nav
    assert "⚠️" not in nav


def test_nav_more_dropdown_elemente(tmp_path: Path):
    """Prüft Summary und Untereinträge im Mehr-Menü."""
    html = _get(tmp_path, "/").text
    nav = _nav(html)
    mehr = nav.split('<details class="nav-more">')[1]

    assert '<summary class="nav-tab">Mehr</summary>' in mehr
    assert '<div class="nav-more-list">' in mehr

    for href, label in [
        ('href="/duplicates"', "Duplikate"),
        ('href="/rejected"', "Quarantäne"),
    ]:
        assert href in mehr
        assert label in mehr


def test_nav_css_regeln_vorhanden(tmp_path: Path):
    """Prüft, dass das erforderliche CSS für das Details-Dropdown vorhanden ist."""
    html = _get(tmp_path, "/").text
    assert ".nav-more { position: relative; display: inline-block; }" in html
    assert ".nav-more summary { cursor: pointer; list-style: none; user-select: none; }" in html
    assert ".nav-more summary::-webkit-details-marker { display: none; }" in html
    assert ".nav-more-list {" in html
    assert ".nav-more-list .nav-tab { display: block; width: 100%; text-align: left; }" in html


def test_aktive_seiten_markierung(tmp_path: Path):
    """Prüft, dass die jeweilige Seite als active markiert ist."""
    routen = [
        ("/", 'href="/" class="nav-tab active"'),
        ("/tracks", 'href="/tracks" class="nav-tab active"'),
        ("/digest", 'href="/digest" class="nav-tab active"'),
        ("/mix", 'href="/mix" class="nav-tab active"'),
        ("/kaufliste", 'href="/kaufliste" class="nav-tab active"'),
        ("/intake", 'href="/intake" class="nav-tab active"'),
        ("/duplicates", 'href="/duplicates" class="nav-tab active"'),
        ("/rejected", 'href="/rejected" class="nav-tab active"'),
    ]
    for pfad, marker in routen:
        resp = _get(tmp_path, pfad)
        assert resp.status_code == 200
        assert marker in resp.text, f"{marker} fehlt auf {pfad}"


def test_konfiguration_sichtbarkeit(tmp_path: Path):
    """Der Konfigurations-Zugang ist das Zahnrad im Header, nicht mehr das Mehr-Menü.

    Es gibt nur diesen einen Weg; ohne Editor (base_path und local_path) fehlt er ganz,
    weil /config dann 404 liefert.
    """
    # Ohne Editor: weder Zahnrad noch Link im Mehr-Menü
    resp_without = client_for(tmp_path).get("/", auth=("sc", PASSWORD))
    assert 'href="/config"' not in resp_without.text
    assert 'class="gear-link' not in resp_without.text

    from fastapi.testclient import TestClient
    from sc_digger.web.app import create_app
    from tests.test_web_tracks_ui import ROOT, make_cfg

    local_path = tmp_path / "config.local.yaml"
    local_path.write_text("{}", encoding="utf-8")
    cfg = make_cfg(tmp_path)
    app = create_app(
        cfg,
        password=PASSWORD,
        base_path=ROOT / "config.yaml",
        local_path=local_path,
    )
    resp_with = TestClient(app).get("/", auth=("sc", PASSWORD))
    nav_with = _nav(resp_with.text)
    mehr_with = nav_with.split('<details class="nav-more">')[1]
    assert 'href="/config"' not in mehr_with
    assert "Konfiguration" not in mehr_with
    assert 'title="Konfiguration" aria-label="Konfiguration"' in resp_with.text


def test_tracks_chips_bereinigt(tmp_path: Path):
    """Prüft, dass Chips auf /tracks die URL nicht mehr in Klammern tragen."""
    html = _get(tmp_path, "/tracks").text
    assert "⚠️ Quarantäne-Liste anzeigen</a>" in html
    assert "👥 Duplikate</a>" in html
    assert "(/rejected)" not in html
    assert "(/duplicates)" not in html
