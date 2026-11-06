"""Eigene Tests für die Toast-Rückmeldungen im Web-Dashboard (Issue #490).

Prüft die globale Meldungsleiste (#toast-region), Barrierefreiheit (role="status",
aria-live="polite"), XSS-Sicherheit (textContent statt innerHTML) und die Toast-Aufrufe
nach Aktionen auf den Seiten tracks.html, kaufliste.html und rejected.html.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_web_tracks_ui import PASSWORD, client_for

TEMPLATES = Path(__file__).resolve().parents[1] / "sc_digger" / "web" / "templates"


def test_toast_region_in_base_template():
    """base.html enthält den Meldungscontainer mit Barrierefreiheits-Attributen."""
    html = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert '<div id="toast-region" role="status" aria-live="polite"></div>' in html


def test_toast_css_classes():
    """CSS-Regeln für die Toast-Region und Statusvarianten sind vorhanden."""
    html = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "#toast-region" in html
    assert ".toast" in html
    assert ".toast-ok" in html
    assert ".toast-err" in html
    assert "position: fixed" in html


def test_show_toast_implementation_safety():
    """showToast setzt textContent und niemals innerHTML (XSS-Schutz)."""
    html = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    start = html.index("function showToast(")
    block = html[start : start + 600]
    assert "textContent" in block
    assert "innerHTML" not in block
    assert "toast toast-" in block
    assert 'kind === "err"' in block
    # Prüft Timeout-Dauern: 8000 ms für Fehler, 4000 ms für Erfolg
    assert "8000" in block
    assert "4000" in block


@pytest.mark.parametrize("route", ["/", "/tracks", "/kaufliste", "/rejected"])
def test_toast_region_present_on_rendered_pages(tmp_path, route):
    """Jede gerenderte Seite erbt die Toast-Region und showToast über base.html."""
    client = client_for(tmp_path)
    res = client.get(route, auth=("sc", PASSWORD))
    assert res.status_code == 200
    assert 'id="toast-region"' in res.text
    assert 'role="status"' in res.text
    assert 'aria-live="polite"' in res.text
    assert "function showToast(" in res.text


def test_tracks_feedback_toast_calls():
    """tracks.html ruft showToast mit den exakten Meldungen für Erfolg und Fehler."""
    html = (TEMPLATES / "tracks.html").read_text(encoding="utf-8")
    assert 'showToast("Bewertung gespeichert", "ok")' in html
    assert 'showToast("Speichern fehlgeschlagen", "err")' in html


def test_kaufliste_feedback_toast_calls():
    """kaufliste.html ruft showToast mit den exakten Meldungen für Erfolg und Fehler."""
    html = (TEMPLATES / "kaufliste.html").read_text(encoding="utf-8")
    assert 'showToast("Bewertung gespeichert", "ok")' in html
    assert 'showToast("Speichern fehlgeschlagen", "err")' in html


def test_rejected_retry_toast_calls():
    """rejected.html ruft showToast bei Freigabe-Erfolg und -Fehler auf."""
    html = (TEMPLATES / "rejected.html").read_text(encoding="utf-8")
    assert 'showToast("Track zur erneuten Prüfung freigegeben", "ok")' in html
    assert 'showToast("Freigabe fehlgeschlagen", "err")' in html


@pytest.mark.parametrize("name", ["mix.html", "intake.html", "config.html"])
def test_unrelated_pages_do_not_call_toast(name):
    """Seiten außerhalb des Issue-Umfangs rufen kein showToast auf."""
    html = (TEMPLATES / name).read_text(encoding="utf-8")
    assert "showToast(" not in html
