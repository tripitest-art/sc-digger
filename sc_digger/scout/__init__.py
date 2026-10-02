"""Scouts für Kaufempfehlungen auf Plattformen außerhalb SoundClouds."""
from sc_digger.scout.bandcamp import _parse_rss, fetch_bandcamp_feeds

__all__ = ["_parse_rss", "fetch_bandcamp_feeds"]
