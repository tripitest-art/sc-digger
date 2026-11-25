"""Eigene Tests zu den Deep-Links aus Telegram (Issue 719)."""
from __future__ import annotations

from types import SimpleNamespace

from sc_digger.output import _fmt_track, dashboard_url, track_detail_url
from tests.test_modes import mk


def _cfg(url):
    return SimpleNamespace(raw={"web": {"public_url": url}})


def test_ungueltige_oder_fehlende_adresse():
    assert dashboard_url(SimpleNamespace(raw={}), "/x") is None
    assert dashboard_url(SimpleNamespace(raw={"web": None}), "/x") is None
    assert dashboard_url(_cfg(None), "/x") is None
    assert dashboard_url(_cfg("dash.example"), "/x") is None


def test_mehrere_schraegstriche_am_ende():
    assert dashboard_url(_cfg("https://dash.example//"), "/a") == "https://dash.example/a"


def test_fmt_track_original_link_bleibt_soundcloud():
    t = mk(7)
    from sc_digger.models import DownloadKind
    t.download_kind = DownloadKind.NATIVE
    text = _fmt_track(t, detail_url="https://dash.example/track/sc/7")
    assert '<a href="https://dash.example/track/sc/7">' in text
    assert f'<a href="{t.url}">Original manuell laden</a>' in text


def test_detail_url_ohne_id():
    assert track_detail_url(_cfg("https://dash.example"), 5) == "https://dash.example/track/sc/5"
