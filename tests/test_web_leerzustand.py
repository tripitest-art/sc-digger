"""Tests für den einheitlichen Leerzustand mit Rücksetz-Link (Issue #489).

Prüft das Makro `empty_state` in `_components.html`, die HTML-Ausgabe auf
den 4 Routen (/tracks, /digest, /rejected, /kaufliste) und das Ausblenden
des Leerzustands bei vorhandenen Treffern.
"""
from pathlib import Path
import pytest
from jinja2 import Environment, FileSystemLoader

from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from tests.test_web_tracks_ui import PASSWORD, client_for

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "sc_digger" / "web" / "templates"


def test_empty_state_macro_rendering():
    """Das Jinja-Makro erzeugt Container, Textabsatz und Rücksetz-Link."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    tmpl = env.from_string(
        "{% from '_components.html' import empty_state %}"
        "{{ empty_state(text, reset_url) }}"
    )
    rendered = tmpl.render(text="Keine Treffer.", reset_url="/tracks")
    assert '<div class="empty-state">' in rendered
    assert "<p>Keine Treffer.</p>" in rendered
    assert '<a class="btn-page" href="/tracks">Filter zurücksetzen</a>' in rendered


@pytest.mark.parametrize(
    "pfad,erwarteter_text",
    [
        ("/tracks", "Keine Treffer für diese Filter."),
        ("/digest", "Keine Vorschläge für diese Filtereinstellungen gefunden."),
        ("/rejected", "Keine abgewiesenen Tracks für diese Filtereinstellungen."),
        ("/kaufliste", "Keine Store-Tracks für diese Filtereinstellungen gefunden."),
    ],
)
def test_leerzustand_auf_allen_routen(tmp_path, pfad, erwarteter_text):
    """Jede der 4 Routen zeigt bei leerer Liste den spezifischen Text und Link."""
    client = client_for(tmp_path)
    res = client.get(pfad, auth=("sc", PASSWORD))
    assert res.status_code == 200
    assert 'class="empty-state"' in res.text
    assert erwarteter_text in res.text
    assert f'href="{pfad}"' in res.text
    assert "Filter zurücksetzen" in res.text


def test_leerzustand_nach_ergebnislosem_filter(tmp_path):
    """Auch bei aktivem Filter ohne Treffer erscheint der Leerzustand mit Rücksetz-Link."""
    client = client_for(tmp_path)
    res = client.get("/tracks?q=unbekannt12345", auth=("sc", PASSWORD))
    assert res.status_code == 200
    assert 'class="empty-state"' in res.text
    assert "Keine Treffer für diese Filter." in res.text
    assert 'href="/tracks"' in res.text
    assert "Filter zurücksetzen" in res.text


def test_kein_leerzustand_wenn_treffer_vorhanden(tmp_path):
    """Wenn Tracks vorhanden sind, wird der Leerzustand nicht angezeigt."""
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.upsert_track(
            TrackRecord(
                path="/music/Schranz/150/test.wav",
                mtime=1.0,
                size=1000,
                artist="Svetec",
                title="Bang",
                format="wav",
                bitrate_kbps=1411.0,
                cutoff_hz=21000,
                quality_status=QualityStatus.OK,
                bpm=150.0,
                key_camelot="8A",
                status=TrackStatus.ARCHIVE,
            )
        )
    client = client_for(tmp_path)
    res = client.get("/tracks", auth=("sc", PASSWORD))
    assert res.status_code == 200
    assert "Svetec" in res.text
    assert 'class="empty-state"' not in res.text
