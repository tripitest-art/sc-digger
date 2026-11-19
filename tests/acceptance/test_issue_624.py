"""Akzeptanztests: Schnellsuche mit der Taste /."""
import json
import re
from pathlib import Path

from sc_digger.web.navigation import NAV_GROUPS, QuickEntry, quick_search_entries
from tests.test_web_tracks_ui import PASSWORD, client_for

ROOT = Path(__file__).resolve().parents[2]
BASE = (ROOT / "sc_digger" / "web" / "templates" / "base.html").read_text(encoding="utf-8")


def test_eintraege_sind_alle_seiten_und_die_konfiguration():
    seiten = [QuickEntry(e.label, e.href, g.label) for g in NAV_GROUPS for e in g.entries]
    assert quick_search_entries() == tuple(seiten)
    assert quick_search_entries(include_config=True) == tuple(
        seiten + [QuickEntry("Konfiguration", "/config", "System")]
    )


def test_jede_seite_hat_die_schnellsuche(tmp_path):
    client = client_for(tmp_path)
    for pfad in ("/", "/digest", "/tracks", "/mix"):
        html = client.get(pfad, auth=("sc", PASSWORD)).text
        dialog = re.search(r'<div id="quick-search"[^>]*>', html)
        assert dialog, pfad
        assert " hidden" in dialog.group(0) and 'role="dialog"' in dialog.group(0)
        daten = re.search(r'<script type="application/json" id="quick-search-data">(.*?)</script>', html, re.S)
        eintraege = json.loads(daten.group(1))
        # Ohne Konfigeditor (client_for) keine Konfiguration, sonst läge /config auf einem 404.
        assert [(e["label"], e["href"], e["group"]) for e in eintraege] == [
            (q.label, q.href, q.group) for q in quick_search_entries()
        ]


def test_nur_navigation_keine_api():
    for eintrag in quick_search_entries(include_config=True):
        assert eintrag.href.startswith("/") and not eintrag.href.startswith("/api/")


def test_taste_und_eingabefelder():
    assert "e.key !== '/'" in BASE
    assert re.search(r"closest\('input, select, textarea, \[contenteditable=\"true\"\]'\)", BASE)
    assert "e.key === 'Escape'" in BASE


def test_freitext_fuehrt_zur_suche_in_tracks_und_digest():
    assert "'Tracks durchsuchen: '" in BASE and "'/tracks?q=' + enc" in BASE
    assert "'Digest durchsuchen: '" in BASE and "'/digest?q=' + enc" in BASE
    assert "encodeURIComponent(" in BASE
