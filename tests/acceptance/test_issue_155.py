"""Akzeptanztests: Bandcamp-RSS-Scout – parst Feeds und erzeugt Track-Objekte."""
from unittest.mock import patch

import pytest

from sc_digger.models import DownloadKind, Track


SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Test Label</title>
    <link>https://testlabel.bandcamp.com</link>
    <item>
      <title>Hard Track</title>
      <link>https://testlabel.bandcamp.com/track/hard-track</link>
      <dc:creator>Test Artist</dc:creator>
      <pubDate>Mon, 29 Sep 2025 10:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Second Track</title>
      <link>https://testlabel.bandcamp.com/track/second-track</link>
      <dc:creator>Another Artist</dc:creator>
      <pubDate>Mon, 30 Sep 2025 10:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""


def test_parse_rss_extracts_tracks():
    """Aus einem gültigen RSS-Feed werden alle Tracks extrahiert."""
    from sc_digger.scout.bandcamp import _parse_rss

    tracks = _parse_rss(SAMPLE_RSS, feed_url="https://testlabel.bandcamp.com/feed")

    assert len(tracks) == 2
    assert all(isinstance(t, Track) for t in tracks)
    assert all(t.download_kind == DownloadKind.STORE for t in tracks)
    assert all(t.purchase_title == "Bandcamp" for t in tracks)
    assert tracks[0].title == "Hard Track"
    assert tracks[0].artist == "Test Artist"
    assert tracks[0].purchase_url == "https://testlabel.bandcamp.com/track/hard-track"


def test_parse_rss_skips_items_without_link():
    """Items ohne <link> werden übergangen, nicht als Fehler behandelt."""
    bad_rss = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item><title>Ohne Link</title></item>
    <item><title>Mit Link</title><link>https://test.bandcamp.com/track/mit-link</link></item>
  </channel>
</rss>"""
    from sc_digger.scout.bandcamp import _parse_rss

    tracks = _parse_rss(bad_rss, feed_url="https://test.bandcamp.com/feed")
    assert len(tracks) == 1
    assert tracks[0].title == "Mit Link"


def test_fetch_bandcamp_feeds_requests_correctly():
    """fetch_bandcamp_feeds() ruft für jede Feed-URL requests.get auf."""
    from sc_digger.scout.bandcamp import fetch_bandcamp_feeds

    class FakeResponse:
        status_code = 200
        text = SAMPLE_RSS
        def raise_for_status(self):
            pass

    with patch("sc_digger.scout.bandcamp.requests.get", return_value=FakeResponse()) as mock_get:
        tracks = fetch_bandcamp_feeds(["https://label1.bandcamp.com/feed", "https://label2.bandcamp.com/feed"])

    assert mock_get.call_count == 2
    assert len(tracks) == 4  # 2 Tracks pro Feed × 2 Feeds


def test_fetch_bandcamp_feeds_survives_one_bad_feed():
    """Wenn ein Feed fehlschlägt, werden die anderen trotzdem verarbeitet."""
    from sc_digger.scout.bandcamp import fetch_bandcamp_feeds

    class GoodResponse:
        status_code = 200
        text = SAMPLE_RSS
        def raise_for_status(self):
            pass

    with patch("sc_digger.scout.bandcamp.requests.get") as mock_get:
        mock_get.side_effect = [Exception("Timeout"), GoodResponse()]
        tracks = fetch_bandcamp_feeds(["https://bad.bandcamp.com/feed", "https://good.bandcamp.com/feed"])

    assert len(tracks) == 2  # Nur der gute Feed


def test_track_ids_are_stable():
    """Track-IDs sind deterministisch (Hash aus purchase_url)."""
    from sc_digger.scout.bandcamp import _parse_rss

    tracks1 = _parse_rss(SAMPLE_RSS, feed_url="https://test.bandcamp.com/feed")
    tracks2 = _parse_rss(SAMPLE_RSS, feed_url="https://test.bandcamp.com/feed")

    assert tracks1[0].id == tracks2[0].id  # gleiche URL → gleicher Hash
    assert tracks1[0].id != tracks2[1].id  # verschiedene URLs → verschiedene Hashs
