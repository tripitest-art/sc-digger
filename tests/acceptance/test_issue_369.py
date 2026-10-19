"""Akzeptanztest: Die Spam-Score-Tests hängen nicht von der echten Track-DB des Rechners ab."""
from pathlib import Path

import tests.test_spam as spam_tests
from sc_digger.db import ArtistReputation

SERVER_DB = "/data/tracks.sqlite"


def _server_db_simulieren(monkeypatch):
    """Auf dem Server existiert die DB aus config.yaml und kennt „artist1“ als bewährten Künstler.

    Gibt die Liste zurück, in die jeder Zugriff auf die DB eingetragen wird.
    """
    geoeffnet: list[str] = []

    class FakeTrackDB:
        def __init__(self, path):
            geoeffnet.append(str(path))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_artist_reputations(self):
            return {"artist1": ArtistReputation(artist="artist1", likes=4)}

    original = Path.exists

    def exists(self, *args, **kwargs):
        if str(self).replace("\\", "/") == SERVER_DB:
            return True
        return original(self, *args, **kwargs)

    monkeypatch.setattr("sc_digger.pipeline.TrackDB", FakeTrackDB)
    monkeypatch.setattr(Path, "exists", exists)
    return geoeffnet


def test_config_ohne_spam_keys_unabhaengig_von_server_db(monkeypatch):
    geoeffnet = _server_db_simulieren(monkeypatch)
    spam_tests.test_config_without_spam_keys_preserves_score()
    assert geoeffnet == []


def test_apply_filter_false_unabhaengig_von_server_db(monkeypatch):
    geoeffnet = _server_db_simulieren(monkeypatch)
    spam_tests.test_apply_filter_false_applies_penalty()
    assert geoeffnet == []
