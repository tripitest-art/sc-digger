"""Akzeptanztests: Referenz-Accounts mit einer einzigen /resolve-Anfrage auflösen."""
import pytest

from sc_digger.soundcloud import SoundCloudError
from tests.test_modes import FakeSC

PROFILE = "https://soundcloud.com/dj-x"


class FakeLib:
    def __init__(self):
        self.calls = []

    def get_user_reposts(self, user_id):
        self.calls.append(("reposts", user_id))
        return iter([])

    def get_user_likes(self, user_id):
        self.calls.append(("likes", user_id))
        return iter([])


def _sc(info):
    sc = FakeSC({PROFILE: info})
    sc._lib = FakeLib()
    return sc


def _resolves(sc):
    return [c for c in sc.calls if c[0] == "/resolve"]


def test_reference_activity_resolves_profile_once():
    sc = _sc({"kind": "user", "id": 7})
    assert sc.reference_activity(PROFILE, 14) == []
    assert len(_resolves(sc)) == 1
    assert sc._lib.calls == [("reposts", 7), ("likes", 7)]


@pytest.mark.parametrize("kind", ["track", "playlist", "system-playlist", None])
def test_non_user_profile_raises_after_one_request(kind):
    sc = _sc({"kind": kind, "id": 7})
    with pytest.raises(SoundCloudError, match="Kein User-Profil"):
        sc.reference_activity(PROFILE, 14)
    assert len(_resolves(sc)) == 1
    assert sc._lib.calls == []
