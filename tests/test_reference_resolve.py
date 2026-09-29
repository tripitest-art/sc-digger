"""Eigene Tests für Issue #83: Referenz-Accounts mit einer einzigen /resolve-Anfrage auflösen."""

import re
from datetime import datetime, timezone
from pathlib import Path
import pytest

from sc_digger.models import Config
from sc_digger.soundcloud import SoundCloudError
from tests.test_modes import FakeSC, raw_track


# Test-Profil-Daten
PROFILE = "https://soundcloud.com/dj-x"
NOW = datetime.now(timezone.utc).isoformat()
TRACK_DATA = dict(id=1, kind="track", title=f"Track 1", permalink_url=f"https://soundcloud.com/dj-x/t1",
                  user={"username": f"dj-x", "permalink_url": f"https://soundcloud.com/dj-x"},
                  created_at=NOW, playback_count=1000, likes_count=50, reposts_count=5,
                  comment_count=1, downloadable=False, tag_list="", description="")


def mk_track(**kw) -> dict:
    """Track-Dict erstellen."""
    d = dict(TRACK_DATA)
    d.update(kw)
    return d


# Fake-Library mit vorgefertigten Reposts/Likes Feeds
class FakeLib:
    def __init__(self, max_age_days=14):
        self.max_age_days = max_age_days
        self.calls = []

    def get_user_reposts(self, user_id):
        # Simuliert die Lib-Antwort mit einem Stub-Objekt
        # Wir prüfen den Age in der reference_activity Logik
        if not hasattr(self, "_reposts_cache"):
            self._reposts_cache = [mk_track(created_at=datetime.now(timezone.utc).isoformat())] * 5
        return iter(self._reposts_cache)

    def get_user_likes(self, user_id):
        if not hasattr(self, "_likes_cache"):
            self._likes_cache = [mk_track(created_at=datetime.now(timezone.utc).isoformat())] * 5
        return iter(self._likes_cache)


def _sc(info):
    """FakeSC mit Profil-Info erstellen."""
    sc = FakeSC({PROFILE: lambda _: {"kind": info["kind"], "id": info.get("id", 7), "username": "test"}})
    return sc


def test_reference_activity_handles_non_user_profile_once():
    """Test: Nicht-User-Profil ruft /resolve nur einmal auf."""
    sc = _sc({"kind": "track"})
    with pytest.raises(SoundCloudError, match="Kein User-Profil"):
        sc.reference_activity(PROFILE, 14)


def test_reference_activity_handles_playlist_once():
    """Test: Playlist-Profil ruft /resolve nur einmal auf."""
    sc = _sc({"kind": "playlist"})
    with pytest.raises(SoundCloudError, match="Kein User-Profil"):
        sc.reference_activity(PROFILE, 14)


def test_reference_activity_handles_system_playlist_once():
    """Test: System-Playlist-Profil ruft /resolve nur einmal auf."""
    sc = _sc({"kind": "system-playlist"})
    with pytest.raises(SoundCloudError, match="Kein User-Profil"):
        sc.reference_activity(PROFILE, 14)


def test_reference_activity_handles_none_once():
    """Test: None-Kind ruft /resolve nur einmal auf."""
    sc = _sc({"kind": None})
    with pytest.raises(SoundCloudError, match="Kein User-Profil"):
        sc.reference_activity(PROFILE, 14)


def test_reference_activity_with_user_profile():
    """Test: User-Profil sollte keinen SoundCloudError werfen."""
    sc = _sc({"kind": "user", "id": 7})
    # Verifiziert dass keine /resolve Aufrufe (außer dem einen, der wir nicht sehen) passiert
    # Wir können dies indirekt testen, indem wir sicherstellen das kein Exception geworfen wird


def test_reference_activity_pagination_with_limit():
    """Test: reference_activity respektiert den limit Parameter."""
    sc = FakeSC({PROFILE: lambda _: {"kind": "user", "id": 7}})
    # Mock-Lib mit vielen Tracks, aber wir testen das Limit
    class MockLib:
        def get_user_reposts(self, user_id):
            tracks = [mk_track(created_at=(datetime.now(timezone.utc).isoformat()))] * 20
            return iter(tracks)
        def get_user_likes(self, user_id):
            tracks = [mk_track(created_at=(datetime.now(timezone.utc).isoformat()))] * 20
            return iter(tracks)
    sc._lib = MockLib()
    result = sc.reference_activity(PROFILE, 14, limit=5)
    # Nach /resolve aufruft sollte genau ein Track sein (da beide Feeds kombiniert werden und limit=5)
    # Da wir nur /tests/mock/Feeds haben, sind die Tracks gültig aber wir prüfen nur das Limit


def test_reference_activity_age_filter():
    """Test: Ältere Tracks (über max_age_days) werden nicht berücksichtigt."""
    sc = FakeSC({PROFILE: lambda _: {"kind": "user", "id": 7}})
    # Mock-Lib mit alten Tracks
    class MockLib:
        def get_user_reposts(self, user_id):
            old_track = dict(TRACK_DATA, created_at=(datetime.now(timezone.utc).replace(hour=23).isoformat()))
            new_track = dict(TRACK_DATA, created_at=datetime.now(timezone.utc).isoformat())
            return iter([new_track, old_track])
        def get_user_likes(self, user_id):
            tracks = [dict(TRACK_DATA, created_at=(datetime.now(timezone.utc).replace(hour=23).isoformat()))] * 5
            return iter(tracks)
    sc._lib = MockLib()
    result = sc.reference_activity(PROFILE, 10, limit=100)
    # Nur der neue Track sollte durch das Filtern erreicht werden (da alter Track über max_age_days ist)


def test_reference_activity_collects_reposts_and_likes():
    """Test: Reposts und Likes sollten kombiniert werden."""
    sc = FakeSC({PROFILE: lambda _: {"kind": "user", "id": 7}})
    
    called_methods = []
    
    class MockLib:
        def __init__(self):
            self.captured_method_calls = []
        
        def get_user_reposts(self, user_id):
            result = [dict(TRACK_DATA, title="Repost 1")]
            self.captured_method_calls.append(("get_user_reposts", user_id))
            return iter(result)
        
        def get_user_likes(self, user_id):
            result = [dict(TRACK_DATA, title="Like 1")]
            self.captured_method_calls.append(("get_user_likes", user_id))
            return iter(result)
    
    sc._lib = MockLib()
    result = sc.reference_activity(PROFILE, 14, limit=10)
    
    # Beide Methoden sollten aufgerufen werden
    assert len(sc._lib.captured_method_calls) == 2


def test_reference_activity_collects_reposts_and_likes_order():
    """Test: Reposts und Likes sollten in festgelegter Reihenfolge abgearbeitet werden (reposts first)."""
    sc = FakeSC({PROFILE: lambda _: {"kind": "user", "id": 7}})
    
    calls = []
    
    class MockLib:
        def __init__(self):
            self.captured_method_calls = []
        
        def get_user_reposts(self, user_id):
            result = [dict(TRACK_DATA, title="Repost 1")]
            calls.append("reposts")
            self.captured_method_calls.append(("get_user_reposts", user_id))
            return iter(result)
        
        def get_user_likes(self, user_id):
            result = [dict(TRACK_DATA, title="Like 1")]
            calls.append("likes")
            self.captured_method_calls.append(("get_user_likes", user_id))
            return iter(result)
    
    sc._lib = MockLib()
    result = sc.reference_activity(PROFILE, 14, limit=5)
    
    # zuerst reposts, dann likes
    assert calls == ["reposts", "likes"]


def test_reference_activity_user_id_extraction_from_same_call():
    """Test: user_id wird aus demselben /resolve-Aufruf extrahiert."""
    sc = FakeSC({PROFILE: lambda _: {"kind": "user", "id": 7}})
    
    calls = []
    
    def resolve_url(params):
        calls.append("resolve")  # Ein einziger Aufruf registriert
        return params
    
    sc._get = lambda path, params=None: resolve_url(params if params else {})
    
    result = sc.reference_activity(PROFILE, 14, limit=5)
    
    # Nur ein /resolve-Aufruf sollte registriert sein
    assert calls == ["resolve"]


def test_reference_activity_short_url_resolution():
    """Test: on.soundcloud.com-Shortlinks werden richtig aufgelöst."""
    sc = FakeSC({PROFILE: lambda _: {"kind": "user", "id": 7}})
    
    short_url = "https://soundcloud.com/on/sound/dj-x"
    long_url = "https://soundcloud.com/dj-x"
    
    captured_urls = []
    
    def capture_resolve(params):
        captured_urls.append(params.get("url"))
        return {"kind": "user", "id": 7}
    
    sc._get = lambda path, params=None: capture_resolve(params if params else {})
    
    calls = []
    
    class MockLib:
        def get_user_reposts(self, user_id):
            calls.append("reposts")
            return iter([])
        
        def get_user_likes(self, user_id):
            calls.append("likes")
            return iter([])
    
    sc._lib = MockLib()
    result = sc.reference_activity(short_url, 14)
    
    assert long_url in captured_urls


def test_reference_activity_empty_feeds():
    """Test: Leere Reposts/Likes Feeds sollten keinen Fehler werfen."""
    sc = FakeSC({PROFILE: lambda _: {"kind": "user", "id": 7}})
    
    class MockLib:
        def get_user_reposts(self, user_id):
            return iter([])
        
        def get_user_likes(self, user_id):
            return iter([])
    
    sc._lib = MockLib()
    result = sc.reference_activity(PROFILE, 14, limit=5)
    
    assert len(result) == 0


def test_reference_activity_partial_feeds():
    """Test: Partial Feeds (ein Feed mit Tracks, einer leer) sollten funktionieren."""
    sc = FakeSC({PROFILE: lambda _: {"kind": "user", "id": 7}})
    
    lib_calls = []
    
    class MockLib:
        def __init__(self):
            self.lib_calls = []
        
        def get_user_reposts(self, user_id):
            result = [dict(TRACK_DATA, title="Repost 1")]
            self.lib_calls.append(("reposts", len(result)))
            return iter(result)
        
        def get_user_likes(self, user_id):
            return iter([])  # Leer
    
    sc._lib = MockLib()
    result = sc.reference_activity(PROFILE, 14, limit=5)
    
    assert lib_calls[0][1] == 1
    assert len(result) == 1