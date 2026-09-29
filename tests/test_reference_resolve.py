"""Eigene Tests für Issue #83: Referenz-Accounts mit einer einzigen /resolve-Anfrage auflösen."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest

from sc_digger import main as m
from sc_digger.soundcloud import SoundCloudError
from tests.test_modes import FakeSC

PROFILE = "https://soundcloud.com/dj-x"


def _make_lib_item(
    track_id: int,
    title: str = "Track",
    created_at: datetime | None = None,
    genre: str = "Schranz",
) -> SimpleNamespace:
    """Erzeugt ein Lib-Item (z. B. Repost/Like) mit den von reference_activity erwarteten Attributen."""
    if created_at is None:
        created_at = datetime.now(timezone.utc)
    track_obj = SimpleNamespace(
        id=track_id,
        kind="track",
        title=title,
        permalink_url=f"https://soundcloud.com/artist/track-{track_id}",
        user=SimpleNamespace(username="Artist", permalink_url="https://soundcloud.com/artist"),
        created_at=created_at,
        duration=180000,
        full_duration=180000,
        genre=genre,
        tag_list="techno schranz",
        description="",
        playback_count=100,
        likes_count=10,
        reposts_count=2,
        comment_count=0,
        downloadable=False,
        has_downloads_left=False,
        purchase_url=None,
        purchase_title=None,
    )
    return SimpleNamespace(created_at=created_at, track=track_obj)


class FakeFeedLib:
    """Fake für die soundcloud-v2-Lib mit konfigurierbaren Reposts und Likes."""

    def __init__(self, reposts=None, likes=None):
        self.reposts = list(reposts or [])
        self.likes = list(likes or [])
        self.calls = []

    def get_user_reposts(self, user_id):
        self.calls.append(("reposts", user_id))
        return iter(self.reposts)

    def get_user_likes(self, user_id):
        self.calls.append(("likes", user_id))
        return iter(self.likes)


def _sc(info, lib=None):
    """FakeSC mit Profil-Info und optionalem FakeFeedLib erstellen."""
    sc = FakeSC({PROFILE: info})
    sc._lib = lib or FakeFeedLib()
    return sc


def _resolves(sc):
    return [c for c in sc.calls if c[0] == "/resolve"]


@pytest.mark.parametrize("kind", ["track", "playlist", "system-playlist", None])
def test_reference_activity_handles_non_user_profile_once(kind):
    """Nicht-User-Profil löst SoundCloudError aus und ruft /resolve genau einmal auf."""
    sc = _sc({"kind": kind})
    with pytest.raises(SoundCloudError, match="Kein User-Profil"):
        sc.reference_activity(PROFILE, 14)
    assert len(_resolves(sc)) == 1
    assert sc._lib.calls == []


def test_reference_activity_with_user_profile_empty_feeds():
    """User-Profil ohne Likes/Reposts liefert leere Liste."""
    sc = _sc({"kind": "user", "id": 7})
    result = sc.reference_activity(PROFILE, 14, limit=5)
    assert result == []
    assert len(_resolves(sc)) == 1
    assert sc._lib.calls == [("reposts", 7), ("likes", 7)]


def test_reference_activity_pagination_with_limit():
    """reference_activity begrenzt Tracks pro Feed auf den limit-Parameter."""
    reposts = [_make_lib_item(i, title=f"Repost {i}") for i in range(1, 11)]
    likes = [_make_lib_item(i + 20, title=f"Like {i}") for i in range(1, 11)]
    lib = FakeFeedLib(reposts=reposts, likes=likes)
    sc = _sc({"kind": "user", "id": 7}, lib=lib)

    result = sc.reference_activity(PROFILE, 14, limit=5)

    # 5 aus Reposts + 5 aus Likes = 10
    assert len(result) == 10
    assert [t.id for t in result[:5]] == [1, 2, 3, 4, 5]
    assert [t.id for t in result[5:]] == [21, 22, 23, 24, 25]


def test_reference_activity_age_filter():
    """Ältere Tracks (über max_age_days) beenden den Feed vorzeitig."""
    now = datetime.now(timezone.utc)
    fresh = _make_lib_item(1, title="Fresh", created_at=now - timedelta(days=2))
    old = _make_lib_item(2, title="Old", created_at=now - timedelta(days=20))
    subsequent = _make_lib_item(3, title="Subsequent", created_at=now - timedelta(days=1))

    # Sobald old erreicht wird, bricht die Schleife ab; subsequent wird nicht berücksichtigt
    lib = FakeFeedLib(reposts=[fresh, old, subsequent], likes=[])
    sc = _sc({"kind": "user", "id": 7}, lib=lib)

    result = sc.reference_activity(PROFILE, 10, limit=100)

    assert len(result) == 1
    assert result[0].id == 1
    assert result[0].title == "Fresh"


def test_reference_activity_collects_reposts_and_likes():
    """Reposts und Likes werden gesammelt, Reposts zuerst."""
    reposts = [_make_lib_item(1, title="Repost 1")]
    likes = [_make_lib_item(2, title="Like 1")]
    lib = FakeFeedLib(reposts=reposts, likes=likes)
    sc = _sc({"kind": "user", "id": 7}, lib=lib)

    result = sc.reference_activity(PROFILE, 14, limit=10)

    assert [t.id for t in result] == [1, 2]
    assert [c[0] for c in lib.calls] == ["reposts", "likes"]
    assert lib.calls == [("reposts", 7), ("likes", 7)]


def test_reference_activity_partial_feeds():
    """Partial Feeds (z. B. Reposts vorhanden, Likes leer) funktionieren problemlos."""
    reposts = [_make_lib_item(1, title="Repost 1")]
    lib = FakeFeedLib(reposts=reposts, likes=[])
    sc = _sc({"kind": "user", "id": 7}, lib=lib)

    result = sc.reference_activity(PROFILE, 14, limit=5)

    assert len(result) == 1
    assert result[0].id == 1
    assert result[0].title == "Repost 1"
    assert lib.calls == [("reposts", 7), ("likes", 7)]


def test_reference_activity_user_id_extraction_from_same_call():
    """user_id wird direkt aus der Antwort des einzigen /resolve-Aufrufs extrahiert."""
    sc = FakeSC({PROFILE: {"kind": "user", "id": 7}})
    lib = FakeFeedLib()
    sc._lib = lib

    result = sc.reference_activity(PROFILE, 14, limit=5)

    assert result == []
    assert len(_resolves(sc)) == 1
    assert lib.calls == [("reposts", 7), ("likes", 7)]


def test_reference_activity_skips_items_without_track():
    """Items ohne track-Attribut (z. B. Playlist-Reposts) werden übersprungen."""
    valid = _make_lib_item(1, title="Valid Track")
    invalid = SimpleNamespace(created_at=datetime.now(timezone.utc), track=None)
    lib = FakeFeedLib(reposts=[invalid, valid], likes=[])
    sc = _sc({"kind": "user", "id": 7}, lib=lib)

    result = sc.reference_activity(PROFILE, 14, limit=5)

    assert len(result) == 1
    assert result[0].id == 1


def test_reference_activity_missing_user_id_recorded_in_failed():
    """Wenn /resolve bei kind == 'user' keine 'id' liefert, fängt collect_sources den Fehler ab."""
    sc = FakeSC({PROFILE: {"kind": "user"}})  # keine 'id' vorhanden
    sc._lib = FakeFeedLib()
    search_cfg = {
        "tags": [],
        "followed_users": [],
        "reference_accounts": [PROFILE],
        "max_age_days": 14,
        "limit_per_tag": 10,
    }

    d = m.collect_sources(sc, search_cfg)

    assert d.total_sources == 1
    assert d.succeeded == 0
    assert len(d.failed) == 1
    assert f"{PROFILE}: KeyError: 'id'" in d.failed[0]
    assert d.aborted is None
