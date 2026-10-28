"""Akzeptanztest: Login-Sperre nach wiederholten falschen Basic-Auth-Versuchen.

Die Web-App schützt sich allein über HTTP-Basic-Auth (Middleware ``require_login``).
Ohne Sperre lässt sich das Passwort beliebig oft durchprobieren.
"""
from __future__ import annotations

import base64
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.requests import Request

from sc_digger.models import Config
from sc_digger.web.app import (
    LOGIN_MAX_FAILURES,
    FailureTracker,
    client_key,
    create_app,
)

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-rate-limit"


def _app(tmp_path: Path, **kwargs):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return create_app(cfg, password=PASSWORD, **kwargs)


def _basic(passwort: str) -> dict[str, str]:
    return {"authorization": "Basic " + base64.b64encode(f"x:{passwort}".encode()).decode()}


def _falsch(client: TestClient, ip: str | None = None) -> int:
    headers = _basic("falsch")
    if ip is not None:
        headers["x-forwarded-for"] = ip
    return client.get("/api/status", headers=headers).status_code


def test_ohne_tracker_bleibt_alles_beim_alten_verhalten(tmp_path: Path) -> None:
    # Wichtig für die bestehende Suite: ohne eingesetzten Tracker gibt es keine Sperre.
    client = TestClient(_app(tmp_path))
    codes = [_falsch(client) for _ in range(10)]
    assert codes == [401] * 10


def test_nach_zu_vielen_fehlversuchen_429_mit_retry_after(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path, login_limit=FailureTracker()))
    for _ in range(LOGIN_MAX_FAILURES):
        assert _falsch(client) == 401
    gesperrt = client.get("/api/status", headers=_basic("falsch"))
    assert gesperrt.status_code == 429
    assert int(gesperrt.headers["retry-after"]) > 0


def test_richtiges_passwort_setzt_den_zaehler_zurueck(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path, login_limit=FailureTracker()))
    for _ in range(LOGIN_MAX_FAILURES - 1):
        assert _falsch(client) == 401
    assert client.get("/api/status", headers=_basic(PASSWORD)).status_code == 200
    for _ in range(LOGIN_MAX_FAILURES - 1):
        assert _falsch(client) == 401, "Zaehler wurde beim Erfolg nicht zurueckgesetzt"


def test_sperre_gilt_auch_fuer_richtiges_passwort(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path, login_limit=FailureTracker()))
    for _ in range(LOGIN_MAX_FAILURES):
        _falsch(client)
    assert client.get("/api/status", headers=_basic(PASSWORD)).status_code == 429


def test_healthz_bleibt_trotz_sperre_offen(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path, login_limit=FailureTracker()))
    for _ in range(LOGIN_MAX_FAILURES):
        _falsch(client)
    assert client.get("/healthz").status_code == 200


def test_tracker_sperre_laeuft_ab_und_fenster_verfaellt() -> None:
    uhr = {"t": 1000.0}
    tracker = FailureTracker(
        max_failures=2, window_seconds=60.0, lockout_seconds=30.0, clock=lambda: uhr["t"]
    )
    tracker.fail("1.2.3.4")
    assert tracker.is_locked("1.2.3.4") == 0.0
    tracker.fail("1.2.3.4")
    assert tracker.is_locked("1.2.3.4") > 0.0
    uhr["t"] += 31.0
    assert tracker.is_locked("1.2.3.4") == 0.0, "Sperre lief nicht ab"
    tracker.fail("1.2.3.4")
    uhr["t"] += 61.0
    tracker.fail("1.2.3.4")
    assert tracker.is_locked("1.2.3.4") == 0.0, "alter Fehlversuch verfaellt nicht im Fenster"


def test_trust_proxy_trennt_clients_nach_x_forwarded_for(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path, login_limit=FailureTracker(), trust_proxy=True))
    for _ in range(LOGIN_MAX_FAILURES):
        assert _falsch(client, ip="10.0.0.5") == 401
    assert _falsch(client, ip="10.0.0.5") == 429
    assert _falsch(client, ip="10.0.0.6") == 401, "andere IP wurde mitgesperrt"


def test_ohne_trust_proxy_ignoriert_der_zaehler_die_weiterleitung(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path, login_limit=FailureTracker()))
    for nummer in range(LOGIN_MAX_FAILURES):
        assert _falsch(client, ip=f"10.0.0.{nummer + 1}") == 401
    assert _falsch(client, ip="10.0.0.99") == 429, "X-Forwarded-For umging die Sperre"


def _request(peer: str = "192.0.2.7", xff: str | None = None) -> Request:
    """Baut eine Request-Attrappe, damit der Schlüssel ohne echten Socket prüfbar ist."""
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    return Request({"type": "http", "client": (peer, 50000), "headers": headers})


def test_client_key_ohne_trust_proxy_nimmt_den_socket_peer(tmp_path: Path) -> None:
    app = _app(tmp_path, login_limit=FailureTracker(), trust_proxy=False)
    assert app.state.login_limit is not None
    # Ohne trust_proxy zählt der Socket-Peer, der Header bleibt ungeachtet.
    assert client_key(_request(), trust_proxy=False) == "192.0.2.7"
    assert client_key(_request(xff="10.1.1.1"), trust_proxy=False) == "192.0.2.7"
    # Mit trust_proxy zählt die letzte Station der Kette, nicht die vom Client erfundene erste.
    assert client_key(_request(xff="10.1.1.1, 10.9.9.9"), trust_proxy=True) == "10.9.9.9"


def test_startbefehl_schaltet_die_sperre_ein() -> None:
    quelle = (ROOT / "sc_digger" / "web" / "__main__.py").read_text(encoding="utf-8")
    assert "FailureTracker(" in quelle, "python -m sc_digger.web setzt keinen FailureTracker ein"
    assert "SC_DIGGER_WEB_RATE_LIMIT" in quelle, "Abschalter fuer die Sperre fehlt"
