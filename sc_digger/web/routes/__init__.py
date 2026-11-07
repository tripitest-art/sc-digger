"""Bereichs-Router für das Web-Dashboard."""
from __future__ import annotations

from . import tracks

BEREICHE = ("status", "tracks", "mix", "digest", "intake", "config")


def register(app) -> None:
    """Hängt die bereits ausgelagerten Bereichs-Router an die App."""
    tracks.register(app)
