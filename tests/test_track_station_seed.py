"""Unit-Tests für SoundCloudClient.track_station mit Seed-Verarbeitung (#439)."""
from unittest.mock import MagicMock

import pytest

from sc_digger.models import Track
from sc_digger.soundcloud import SoundCloudClient


def _make_track(track_id: int, title: str = "Title", artist: str = "Artist", downloadable: bool = True) -> Track:
    return Track(
        id=track_id, title=title, url=f"https://soundcloud.com/test/{track_id}",
        artist=artist, artist_url="", created_at="2026-10-31T00:00:00Z", duration_ms=180000,
        genre="Schranz", tags=[], description="", bpm=150.0, plays=100, likes=10,
        reposts=2, comments=1, downloadable=downloadable, has_downloads_left=True,
        purchase_url=None, purchase_title=None,
    )


def test_station_ohne_duplikat_stellt_seed_voran():
    client = SoundCloudClient()
    seed = _make_track(100, "Seed Track", "Seed Artist", downloadable=True)
    station_tracks = [_make_track(101), _make_track(102), _make_track(103)]

    client.resolve_track = MagicMock(return_value=seed)
    client.station = MagicMock(return_value=station_tracks)

    label, tracks = client.track_station("https://soundcloud.com/artist/seed", limit=10)

    assert label == "Seed Artist – Seed Track"
    assert [t.id for t in tracks] == [100, 101, 102, 103]
    client.resolve_track.assert_called_once_with("https://soundcloud.com/artist/seed")
    client.station.assert_called_once_with(100, 10)


def test_station_mit_duplikat_entfernt_duplikat():
    client = SoundCloudClient()
    seed = _make_track(200, "Seed", "Artist")
    # Station liefert das Seed-Track-Duplikat mitten in den Ergebnissen
    station_tracks = [_make_track(201), _make_track(200), _make_track(202)]

    client.resolve_track = MagicMock(return_value=seed)
    client.station = MagicMock(return_value=station_tracks)

    label, tracks = client.track_station("https://soundcloud.com/artist/seed", limit=5)

    assert [t.id for t in tracks] == [200, 201, 202]
    assert tracks.count(seed) == 1


def test_leere_station_liefert_nur_seed():
    client = SoundCloudClient()
    seed = _make_track(300, "Lone Seed", "Artist")

    client.resolve_track = MagicMock(return_value=seed)
    client.station = MagicMock(return_value=[])

    label, tracks = client.track_station("https://soundcloud.com/artist/seed")

    assert label == "Artist – Lone Seed"
    assert tracks == [seed]
