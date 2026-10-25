import pytest

from sc_digger import soundcloud as sc_mod
from sc_digger.soundcloud import SoundCloudClient

NetError = sc_mod.requests.ConnectionError
SlowError = sc_mod.requests.Timeout
URL = "https://soundcloud.com"


class Resp:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.text = ""


class FlakySession:
    def __init__(self, fails, exc=None, status=200):
        self.fails = fails
        self.exc = exc or NetError("NameResolutionError")
        self.status = status
        self.calls = 0
        self.headers = {}

    def get(self, url, **kw):
        self.calls += 1
        if self.fails > 0:
            self.fails -= 1
            raise self.exc
        return Resp(self.status)


@pytest.fixture
def waits(monkeypatch):
    seen = []
    monkeypatch.setattr(sc_mod.time, "sleep", lambda s: seen.append(s))
    return seen


def make(session):
    client = SoundCloudClient(request_delay=0)
    client.s = session
    return client


def test_backoff_schedule_covers_a_two_minute_outage():
    assert sc_mod.NET_BACKOFF_S == (5, 15, 45, 90)
    assert sum(sc_mod.NET_BACKOFF_S) >= 120


def test_new_client_is_not_marked_down():
    assert SoundCloudClient(request_delay=0).net_down is False


def test_outage_of_one_minute_is_survived(waits):
    session = FlakySession(fails=3)
    client = make(session)
    assert client._net_get(URL).status_code == 200
    assert session.calls == 4
    assert waits == [5, 15, 45]
    assert client.net_down is False


def test_persistent_outage_gives_up_after_all_waits(waits):
    session = FlakySession(fails=99)
    client = make(session)
    with pytest.raises(NetError):
        client._net_get(URL)
    assert session.calls == len(sc_mod.NET_BACKOFF_S) + 1
    assert waits == list(sc_mod.NET_BACKOFF_S)
    assert client.net_down is True


def test_after_giving_up_next_requests_fail_fast(waits):
    session = FlakySession(fails=99)
    client = make(session)
    with pytest.raises(NetError):
        client._net_get(URL)
    waits.clear()
    session.calls = 0
    with pytest.raises(NetError):
        client._net_get(URL)
    assert session.calls == 1
    assert waits == []


def test_success_after_giving_up_restores_normal_retries(waits):
    session = FlakySession(fails=99)
    client = make(session)
    with pytest.raises(NetError):
        client._net_get(URL)
    session.fails = 0
    assert client._net_get(URL).status_code == 200
    assert client.net_down is False
    waits.clear()
    session.fails, session.calls = 2, 0
    assert client._net_get(URL).status_code == 200
    assert waits == [5, 15]


def test_timeout_is_retried_like_a_connection_error(waits):
    session = FlakySession(fails=1, exc=SlowError("zu langsam"))
    client = make(session)
    assert client._net_get(URL).status_code == 200
    assert waits == [5]


def test_http_status_codes_are_not_retried(waits):
    session = FlakySession(fails=0, status=500)
    client = make(session)
    assert client._net_get(URL).status_code == 500
    assert session.calls == 1
    assert waits == []
