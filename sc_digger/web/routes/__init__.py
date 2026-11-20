"""Bereichs-Router für das Web-Dashboard."""
from __future__ import annotations

from .. import navigation, timefmt
from . import mixesdb, preview, track_detail, tracks

BEREICHE = ("status", "tracks", "mix", "digest", "intake", "config")


def register(app) -> None:
    """Hängt die bereits ausgelagerten Bereichs-Router an die App."""
    tracks.register(app)
    # Die Detailseite ist kein eigener Bereich (steht nicht in BEREICHE), nur eine Route.
    track_detail.register(app)
    # Hörproben aus dem Stream (eigener Schalter web.stream_preview, Standard aus).
    preview.register(app)
    # MixesDB-Abgleich (Set-Tracklisten aus dem Szene-Wiki, rein lesend).
    mixesdb.register(app)
    navigation.register(app)
    timefmt.register(app)
