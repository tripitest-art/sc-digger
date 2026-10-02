"""Akzeptanztests: Beatport-Chart-Scout – scraped Charts und erzeugt Track-Objekte."""
from unittest.mock import patch

import pytest

from sc_digger.models import DownloadKind, Track


BEATPORT_CHART_HTML = """<!DOCTYPE html>
<html>
<body>
  <h1>Hard Techno Top 100</h1>
  <div class="tracks">
    <div class="bucket-item ec-item track" data-track="{&quot;id&quot;:123,&quot;title&quot;:&quot;Sledgehammer&quot;,&quot;artists&quot;:[&quot;Test Artist&quot;],&quot;slug&quot;:&quot;sledgehammer&quot;}">
      <a href="https://www.beatport.com/track/sledgehammer/123">
        <span class="buk-track-title">Sledgehammer</span>
        <span class="buk-track-artists">Test Artist</span>
      </a>
    </div>
    <div class="bucket-item ec-item track" data-track="{&quot;id&quot;:456,&quot;title&quot;:&quot;Dark Ritual&quot;,&quot;artists&quot;:[&quot;Another Artist&quot;],&quot;slug&quot;:&quot;dark-ritual&quot;}">
      <a href="https://www.beatport.com/track/dark-ritual/456">
        <span class="buk-track-title">Dark Ritual</span>
        <span class="buk-track-artists">Another Artist</span>
      </a>
    </div>
  </div>
</body>
</html>
"""


def test_parse_chart_html_extracts_tracks():
    """Aus einer gültigen Chart-Seite werden alle Tracks extrahiert."""
    from sc_digger.scout.beatport import _parse_chart_html

    tracks = _parse_chart_html(BEATPORT_CHART_HTML, chart_url="https://www.beatport.com/genre/hard-techno/2/top-100")

    assert len(tracks) == 2
    assert all(isinstance(t, Track) for t in tracks)
    assert all(t.download_kind == DownloadKind.STORE for t in tracks)
    assert all(t.purchase_title == "Beatport" for t in tracks)
    assert tracks[0].title == "Sledgehammer"
    assert tracks[0].artist == "Test Artist"
    assert "beatport.com/track/" in tracks[0].purchase_url


def test_parse_chart_html_skips_items_without_link():
    """Zeilen ohne gültigen Track-Link werden übergangen."""
    bad_html = """<html><body><div class="tracks">
      <div class="bucket-item ec-item track">Kein Link</div>
      <div class="bucket-item ec-item track"><a href="https://www.beatport.com/track/link/123">Link da</a></div>
    </div></body></html>"""
    from sc_digger.scout.beatport import _parse_chart_html

    tracks = _parse_chart_html(bad_html, chart_url="https://www.beatport.com/test")
    assert len(tracks) == 1


def test_fetch_beatport_charts_requests_correctly():
    """fetch_beatport_charts() ruft für jede Chart-URL requests.get auf."""
    from sc_digger.scout.beatport import fetch_beatport_charts

    class FakeResponse:
        status_code = 200
        text = BEATPORT_CHART_HTML
        def raise_for_status(self):
            pass

    with patch("sc_digger.scout.beatport.requests.get", return_value=FakeResponse()) as mock_get:
        tracks = fetch_beatport_charts(["https://www.beatport.com/genre/hard-techno/2/top-100"])

    assert mock_get.call_count == 1
    assert len(tracks) == 2


def test_fetch_beatport_charts_survives_one_bad_chart():
    """Wenn eine Chart-URL fehlschlägt, werden die anderen trotzdem verarbeitet."""
    from sc_digger.scout.beatport import fetch_beatport_charts

    class GoodResponse:
        status_code = 200
        text = BEATPORT_CHART_HTML
        def raise_for_status(self):
            pass

    with patch("sc_digger.scout.beatport.requests.get") as mock_get:
        mock_get.side_effect = [Exception("Timeout"), GoodResponse()]
        tracks = fetch_beatport_charts(["https://bad.beatport.com/chart", "https://good.beatport.com/chart"])

    assert len(tracks) == 2  # Nur die gute Chart


def test_track_ids_are_stable():
    """Track-IDs sind deterministisch (Hash aus purchase_url)."""
    from sc_digger.scout.beatport import _parse_chart_html

    tracks1 = _parse_chart_html(BEATPORT_CHART_HTML, chart_url="https://www.beatport.com/genre/hard-techno/2/top-100")
    tracks2 = _parse_chart_html(BEATPORT_CHART_HTML, chart_url="https://www.beatport.com/genre/hard-techno/2/top-100")

    assert tracks1[0].id == tracks2[0].id
    assert tracks1[0].id != tracks2[1].id
