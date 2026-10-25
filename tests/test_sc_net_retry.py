"""Netzwerk-Retry im SoundCloud-Client: kurzer DNS-Aussetzer darf keine Quelle kosten."""
import pytest
import requests

from sc_digger import soundcloud as sc_mod
from sc_digger.soundcloud import SoundCloudClient


class Resp:
    status_code = 200
    text = ""

    def raise_for_status(self):
        pass

    def json(self):
        return {"ok": True}


class FlakySession:
    def __init__(self, fails, exc=requests.ConnectionError("NameResolutionError")):
        self.fails, self.exc, self.calls = fails, exc, 0
        self.headers = {}

    def get(self, url, **kw):
        self.calls += 1
        if self.calls <= self.fails:
            raise self.exc
        return Resp()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(sc_mod.time, "sleep", lambda s: None)
    c = SoundCloudClient(request_delay=0)
    c.client_id = "x" * 32
    return c


def test_get_wiederholt_bei_connectionerror(client):
    client.s = FlakySession(fails=2)
    assert client._get("/foo") == {"ok": True}
    assert client.s.calls == 3


def test_get_wiederholt_bei_timeout(client):
    client.s = FlakySession(fails=1, exc=requests.Timeout("t"))
    assert client._get("/foo") == {"ok": True}


def test_get_gibt_nach_allen_wartezeiten_auf(client):
    client.s = FlakySession(fails=99)
    with pytest.raises(requests.ConnectionError):
        client._get("/foo")
    assert client.s.calls == len(sc_mod.NET_BACKOFF_S) + 1


def test_client_id_abruf_wiederholt(client):
    client.client_id = None
    client.s = FlakySession(fails=1)
    with pytest.raises(sc_mod.ClientIdError):  # Seite ohne Skripte, aber der Abruf kam durch
        client._fetch_client_id()
    assert client.s.calls == 2
