"""Eigene Tests für die Navigation nach Arbeitsbereichen (Issue #600)."""
from __future__ import annotations

import re

from sc_digger.web import navigation
from sc_digger.web.navigation import NAV_GROUPS, find_active, nav_state
from tests.test_web_tracks_ui import PASSWORD, client_for


def _get(tmp_path, pfad):
    antwort = client_for(tmp_path).get(pfad, auth=("sc", PASSWORD))
    assert antwort.status_code == 200, pfad
    return antwort.text


def _nav(html, klasse):
    treffer = re.search(r'<nav class="' + klasse + r'"[^>]*>(.*?)</nav>', html, re.S)
    return treffer.group(1) if treffer else None


def test_jede_seite_steht_in_genau_einem_bereich():
    hrefs = [e.href for g in NAV_GROUPS for e in g.entries]
    assert len(hrefs) == len(set(hrefs))


def test_find_active_vertraegt_unsinnige_eingaben():
    assert find_active(None) == (None, None)  # type: ignore[arg-type]
    assert find_active("digest") == (None, None)
    assert find_active("/digest/")[1].href == "/digest"
    assert find_active("/api/digest") == (None, None)


def test_nav_state_ohne_anfrage_hat_keinen_aktiven_bereich():
    token = navigation._request_path.set("")
    try:
        zustand = nav_state()
    finally:
        navigation._request_path.reset(token)
    assert zustand.groups == NAV_GROUPS
    assert zustand.active_group is None
    assert zustand.active_entry is None
    assert zustand.sub_entries == ()


def test_nav_state_liefert_unterseiten_nur_bei_mehreren_eintraegen():
    for pfad, erwartet in [("/digest", 3), ("/intake", 2), ("/tracks", 2), ("/", 0), ("/mix", 0)]:
        token = navigation._request_path.set(pfad)
        try:
            assert len(nav_state().sub_entries) == erwartet, pfad
        finally:
            navigation._request_path.reset(token)


def test_kuratoren_markiert_entdecken(tmp_path):
    html = _get(tmp_path, "/kuratoren")
    assert 'href="/digest" class="nav-tab active" data-nav-group="entdecken"' in html
    assert 'href="/kuratoren" class="nav-tab active" aria-current="page"' in _nav(html, "nav-sub")


def test_startseite_markiert_start_ohne_unterleiste(tmp_path):
    html = _get(tmp_path, "/")
    assert 'href="/" class="nav-tab active" data-nav-group="start"' in html
    assert _nav(html, "nav-sub") is None


def test_unterleiste_steht_nach_dem_kopf(tmp_path):
    html = _get(tmp_path, "/tracks")
    assert html.index("</header>") < html.index('<nav class="nav-sub"') < html.index('<main class="site-main">')


def test_nur_ein_aktiver_bereich(tmp_path):
    for pfad in ("/", "/digest", "/intake", "/rejected", "/tracks", "/duplicates", "/mix"):
        leiste = _nav(_get(tmp_path, pfad), "nav-tabs")
        assert leiste.count("nav-tab active") == 1, pfad
