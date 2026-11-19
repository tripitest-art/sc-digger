"""Akzeptanztests: Navigation nach Arbeitsbereichen statt Menü „Mehr“."""
import re
from pathlib import Path

from sc_digger.web.navigation import NAV_GROUPS, find_active
from tests.test_web_tracks_ui import PASSWORD, client_for

ROOT = Path(__file__).resolve().parents[2]
BASE = (ROOT / "sc_digger" / "web" / "templates" / "base.html").read_text(encoding="utf-8")


def _get(tmp_path, pfad):
    antwort = client_for(tmp_path).get(pfad, auth=("sc", PASSWORD))
    assert antwort.status_code == 200, pfad
    return antwort.text


def _nav(html, klasse):
    treffer = re.search(r'<nav class="' + klasse + r'"[^>]*>(.*?)</nav>', html, re.S)
    return treffer.group(1) if treffer else None


def _links(teil):
    return re.findall(r'<a href="([^"]+)" class="([^"]+)"', teil)


def test_bereiche_in_fester_reihenfolge():
    assert [(g.key, g.label) for g in NAV_GROUPS] == [
        ("start", "Start"),
        ("entdecken", "Entdecken"),
        ("eingang", "Eingang"),
        ("bibliothek", "Bibliothek"),
        ("auflegen", "Auflegen"),
    ]
    assert [[e.href for e in g.entries] for g in NAV_GROUPS] == [
        ["/"],
        ["/digest", "/kuratoren", "/kaufliste"],
        ["/intake", "/rejected"],
        ["/tracks", "/duplicates"],
        ["/mix"],
    ]
    assert [g.href for g in NAV_GROUPS] == ["/", "/digest", "/intake", "/tracks", "/mix"]


def test_find_active_ordnet_pfade_zu():
    def kurz(pfad):
        gruppe, eintrag = find_active(pfad)
        return (gruppe.key if gruppe else None, eintrag.href if eintrag else None)

    assert kurz("/") == ("start", "/")
    assert kurz("/digest") == ("entdecken", "/digest")
    assert kurz("/kaufliste") == ("entdecken", "/kaufliste")
    assert kurz("/rejected") == ("eingang", "/rejected")
    assert kurz("/duplicates") == ("bibliothek", "/duplicates")
    assert kurz("/tracks/17") == ("bibliothek", "/tracks")
    assert kurz("/mix") == ("auflegen", "/mix")
    assert kurz("/trackster") == (None, None)
    assert kurz("/config/download") == (None, None)
    assert kurz("") == (None, None)


def test_obere_leiste_zeigt_die_bereiche_und_markiert_den_aktiven(tmp_path):
    leiste = _nav(_get(tmp_path, "/kaufliste"), "nav-tabs")
    assert leiste is not None
    assert _links(leiste) == [
        ("/", "nav-tab"),
        ("/digest", "nav-tab active"),
        ("/intake", "nav-tab"),
        ("/tracks", "nav-tab"),
        ("/mix", "nav-tab"),
    ]
    for label in ("Start", "Entdecken", "Eingang", "Bibliothek", "Auflegen"):
        assert f">{label}</a>" in leiste


def test_unterleiste_zeigt_die_seiten_des_bereichs(tmp_path):
    html = _get(tmp_path, "/kaufliste")
    unter = _nav(html, "nav-sub")
    assert unter is not None, "Unterleiste fehlt"
    assert _links(unter) == [
        ("/digest", "nav-tab"),
        ("/kuratoren", "nav-tab"),
        ("/kaufliste", "nav-tab active"),
    ]
    assert 'aria-current="page"' in unter
    assert unter.count('aria-current="page"') == 1


def test_keine_unterleiste_bei_bereichen_mit_einer_seite(tmp_path):
    assert _nav(_get(tmp_path, "/"), "nav-sub") is None
    assert _nav(_get(tmp_path, "/mix"), "nav-sub") is None


def test_eingang_und_bibliothek_markieren_ihre_seiten(tmp_path):
    unter = _nav(_get(tmp_path, "/rejected"), "nav-sub")
    assert _links(unter) == [("/intake", "nav-tab"), ("/rejected", "nav-tab active")]
    leiste = _nav(_get(tmp_path, "/duplicates"), "nav-tabs")
    assert ("/tracks", "nav-tab active") in _links(leiste)


def test_menue_mehr_entfaellt_und_konfiguration_bleibt_am_zahnrad(tmp_path):
    html = _get(tmp_path, "/digest")
    assert "nav-more" not in html
    # Das Zahnrad erscheint nur bei eingeschaltetem Editor; die Vorlage muss es behalten.
    assert 'class="gear-link' in BASE
    assert "nav-more" not in BASE
    assert 'href="/config"' not in _nav(html, "nav-tabs")
    assert 'href="/config"' not in (_nav(html, "nav-sub") or "")


def test_jede_seite_der_navigation_ist_erreichbar(tmp_path):
    for gruppe in NAV_GROUPS:
        for eintrag in gruppe.entries:
            html = _get(tmp_path, eintrag.href)
            assert '<nav class="nav-tabs">' in html, eintrag.href
