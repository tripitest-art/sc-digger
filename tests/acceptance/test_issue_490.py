"""Akzeptanztests: Toast-Rückmeldung im Dashboard."""
from pathlib import Path

import pytest

from tests.test_web_tracks_ui import PASSWORD, client_for

TEMPLATES = Path(__file__).resolve().parents[2] / "sc_digger" / "web" / "templates"


def test_meldungsleiste_auf_jeder_seite(tmp_path):
    r = client_for(tmp_path).get("/", auth=("sc", PASSWORD))
    assert 'id="toast-region"' in r.text
    assert 'role="status"' in r.text
    assert 'aria-live="polite"' in r.text
    assert "function showToast(" in r.text


def test_toast_setzt_text_nicht_als_html():
    text = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    start = text.index("function showToast(")
    block = text[start:start + 800]
    assert "textContent" in block
    assert "innerHTML" not in block


@pytest.mark.parametrize("name", ["tracks.html", "kaufliste.html", "rejected.html"])
def test_aktionsseiten_rufen_toast(name):
    assert "showToast(" in (TEMPLATES / name).read_text(encoding="utf-8")