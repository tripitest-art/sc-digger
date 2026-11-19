"""Eigene Tests der Schnellsuche (Issue #624)."""
from sc_digger.web.navigation import NAV_GROUPS, QuickEntry, quick_search_entries


def test_seiten_tragen_ihren_bereich():
    eintraege = quick_search_entries()
    assert QuickEntry("Duplikate", "/duplicates", "Bibliothek") in eintraege
    assert QuickEntry("Übersicht", "/", "Start") in eintraege
    assert len(eintraege) == sum(len(g.entries) for g in NAV_GROUPS)


def test_konfiguration_nur_auf_wunsch_und_zuletzt():
    assert all(e.href != "/config" for e in quick_search_entries())
    mit = quick_search_entries(include_config=True)
    assert mit[-1] == QuickEntry("Konfiguration", "/config", "System")
    assert mit[:-1] == quick_search_entries()


def test_keine_aktionen_oder_api_links():
    for e in quick_search_entries(include_config=True):
        assert e.href.startswith("/") and "/api/" not in e.href


def test_links_sind_eindeutig():
    hrefs = [e.href for e in quick_search_entries(include_config=True)]
    assert len(hrefs) == len(set(hrefs))
