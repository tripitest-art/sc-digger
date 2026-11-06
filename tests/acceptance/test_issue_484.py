"""Akzeptanztests: Navigation mit „Mehr“-Menü."""
import re

from tests.test_web_tracks_ui import PASSWORD, client_for


def _get(tmp_path, pfad):
    return client_for(tmp_path).get(pfad, auth=("sc", PASSWORD))


def _nav(html):
    return re.search(r'<nav class="nav-tabs">(.*?)</nav>', html, re.S).group(1)


def test_haupteintraege_ausserhalb_von_mehr(tmp_path):
    nav = _nav(_get(tmp_path, "/").text)
    haupt = nav.split('<details class="nav-more">')[0]
    for ziel in ("/", "/tracks", "/digest", "/mix", "/kaufliste"):
        assert f'href="{ziel}"' in haupt


def test_weitere_ziele_in_mehr(tmp_path):
    nav = _nav(_get(tmp_path, "/").text)
    mehr = nav.split('<details class="nav-more">')[1]
    for ziel in ("/intake", "/duplicates", "/rejected"):
        assert f'href="{ziel}"' in mehr


def test_aktive_seite_duplikate_markiert(tmp_path):
    assert 'href="/duplicates" class="nav-tab active"' in _get(tmp_path, "/duplicates").text


def test_chips_ohne_url_in_klammern(tmp_path):
    text = _get(tmp_path, "/tracks").text
    assert "(/rejected)" not in text
    assert "(/duplicates)" not in text