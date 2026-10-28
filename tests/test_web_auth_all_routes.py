"""Auth-Abdeckung der Web-App: jede registrierte Route verlangt Anmeldung.

Der Test läuft über ``app.routes`` statt über eine handgepflegte Liste. Eine neue Route
ohne Anmeldung lässt ihn rot werden, auch wenn niemand daran gedacht hat, sie hier einzutragen.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import PUBLIC_PATHS, create_app

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-auth-alle-routen"


def _app(tmp_path: Path, password: str = PASSWORD):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    # Beide Pfade gesetzt: auch die Editor-Routen sind registriert.
    return create_app(
        cfg, password=password,
        base_path=ROOT / "config.yaml", local_path=tmp_path / "config.local.yaml",
    )


def _routes(app) -> list[tuple[str, str]]:
    found = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            path = re.sub(r"\{[^}]+\}", "1", route.path)
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                found.append((method, path))
    return found


def _basic(password: str) -> dict[str, str]:
    token = base64.b64encode(f"x:{password}".encode()).decode()
    return {"authorization": f"Basic {token}"}


def test_public_paths_are_exactly_healthz():
    assert PUBLIC_PATHS == frozenset({"/healthz"})


def test_app_registers_a_meaningful_number_of_routes(tmp_path):
    # Schutz davor, dass die Parametrisierung unten stillschweigend ins Leere läuft.
    assert len(_routes(_app(tmp_path))) >= 25


def test_every_route_rejects_missing_and_wrong_credentials(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    unprotected = []
    for method, path in _routes(app):
        if path in PUBLIC_PATHS:
            continue
        for headers in ({}, _basic("falsch")):
            resp = client.request(method, path, headers=headers)
            if resp.status_code != 401:
                unprotected.append((method, path, resp.status_code, bool(headers)))
    assert not unprotected, f"Routen ohne Anmeldepflicht: {unprotected}"


def test_unknown_paths_also_require_login(tmp_path):
    # Default-deny: auch nicht existierende Pfade verraten ohne Anmeldung nichts (kein 404).
    client = TestClient(_app(tmp_path))
    assert client.get("/gibt-es-nicht").status_code == 401


def test_healthz_stays_public(tmp_path):
    assert TestClient(_app(tmp_path)).get("/healthz").status_code == 200


def test_correct_password_passes_the_gate(tmp_path):
    client = TestClient(_app(tmp_path))
    assert client.get("/api/status", headers=_basic(PASSWORD)).status_code != 401


def test_password_with_umlaut_is_not_a_server_error(tmp_path):
    # secrets.compare_digest wirft TypeError bei Nicht-ASCII-str; das ergab HTTP 500
    # statt 401 und machte ein Passwort mit Umlaut unbenutzbar.
    passwd = "Käse123Grün"
    client = TestClient(_app(tmp_path, password=passwd))
    assert client.get("/api/status", headers=_basic(passwd)).status_code == 200
    assert client.get("/api/status", headers=_basic("Kaese123Gruen")).status_code == 401
