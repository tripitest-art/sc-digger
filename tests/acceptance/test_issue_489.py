"""Akzeptanztests: einheitlicher Leerzustand."""
import pytest

from tests.test_web_tracks_ui import PASSWORD, client_for


@pytest.mark.parametrize("pfad", ["/tracks", "/digest", "/rejected", "/kaufliste"])
def test_leerzustand_mit_zuruecksetzen(tmp_path, pfad):
    r = client_for(tmp_path).get(pfad, auth=("sc", PASSWORD))
    assert r.status_code == 200
    assert 'class="empty-state"' in r.text
    assert f'href="{pfad}"' in r.text
    assert "Filter zurücksetzen" in r.text