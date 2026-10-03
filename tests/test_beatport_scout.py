"""Eigene Tests für den Beatport-Chart-Scout (Issue #156).

Deckt Fälle ab, die die Akzeptanztests nicht prüfen: Weitergabe des Timeouts,
Links außerhalb von Beatport und fehlende Titel/Künstler-Elemente.
"""
from unittest.mock import patch

from sc_digger.scout.beatport import fetch_beatport_charts


def test_fetch_passes_timeout_to_requests():
    class FakeResponse:
        status_code = 200
        text = "<html><body></body></html>"

        def raise_for_status(self):
            pass

    with patch("sc_digger.scout.beatport.requests.get", return_value=FakeResponse()) as mock_get:
        fetch_beatport_charts(["https://www.beatport.com/chart"], timeout=7)

    assert mock_get.call_args.kwargs["timeout"] == 7


def test_parse_ignores_non_beatport_links():
    html = """<html><body><div class="tracks">
      <div class="bucket-item track"><a href="https://example.com/werbung">Werbung</a></div>
      <div class="bucket-item track"><a href="https://www.beatport.com/track/echt/1">Echt</a></div>
    </div></body></html>"""
    from sc_digger.scout.beatport import _parse_chart_html

    tracks = _parse_chart_html(html, chart_url="https://www.beatport.com/chart")
    assert len(tracks) == 1  # Fremd-Links werden nicht als Beatport-Track geladen
    assert tracks[0].purchase_url == "https://www.beatport.com/track/echt/1"


def test_parse_resolves_relative_link_against_chart_url():
    html = """<html><body><div class="tracks">
      <div class="bucket-item track"><a href="/track/relativ/7">Relativ</a></div>
    </div></body></html>"""
    from sc_digger.scout.beatport import _parse_chart_html

    tracks = _parse_chart_html(
        html, chart_url="https://www.beatport.com/genre/hard-techno/2/top-100"
    )
    assert len(tracks) == 1
    assert tracks[0].purchase_url == "https://www.beatport.com/track/relativ/7"
    assert tracks[0].url == tracks[0].purchase_url


def test_parse_without_title_and_artist_is_empty_not_crash():
    html = """<html><body><div class="tracks">
      <div class="bucket-item track"><a href="https://www.beatport.com/track/ohne/2"></a></div>
    </div></body></html>"""
    from sc_digger.scout.beatport import _parse_chart_html

    tracks = _parse_chart_html(html, chart_url="https://www.beatport.com/chart")
    assert len(tracks) == 1
    assert tracks[0].title == ""
    assert tracks[0].artist == ""
