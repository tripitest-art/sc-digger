"""Akzeptanztests: Vorperioden-Vergleich auf der Statusseite."""
import pytest

from sc_digger.web.status import change_badge
from tests.test_web_tracks_ui import PASSWORD, client_for


@pytest.mark.parametrize("previous, current, direction, text", [
    (100, 112, "up", "▲ +12 %"),
    (100, 75, "down", "▼ −25 %"),
    (40, 40, "flat", "● 0 %"),
    (0, 10, "none", "–"),
    (None, 10, "none", "–"),
])
def test_change_badge(previous, current, direction, text):
    assert change_badge(previous, current) == {"direction": direction, "text": text}


def test_rundet_auf_ganze_prozent():
    assert change_badge(3, 4)["text"] == "▲ +33 %"


def test_statusseite_laedt_mit_badge_stil(tmp_path):
    r = client_for(tmp_path).get("/", auth=("sc", PASSWORD))
    assert r.status_code == 200
    assert ".change-badge" in r.text
