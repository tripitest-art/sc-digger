"""Eigene Tests für SoundCloud-Auth-Guard (Issue #458)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sc_digger import main as m
from sc_digger.health import Health
from sc_digger.models import Config
from sc_digger.soundcloud import SoundCloudClient, TokenStatus, check_auth_token
from tests.test_modes import CFG


def _cfg(tmp_path):
    cfg = Config(dict(CFG.raw))
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def test_run_discover_dry_run_skips_auth_guard(tmp_path, monkeypatch):
    """Bei dry_run=True darf guard_auth_token nicht aufgerufen werden."""
    monkeypatch.setenv("SOUNDCLOUD_AUTH_TOKEN", "test-token")
    guard_mock = MagicMock()
    monkeypatch.setattr(m, "guard_auth_token", guard_mock)
    discover_mock = MagicMock()
    monkeypatch.setattr(m, "_discover", discover_mock)

    m.run_discover(_cfg(tmp_path), dry_run=True, no_telegram=True)

    assert guard_mock.call_count == 0
    assert discover_mock.call_count == 1


def test_health_evaluate_hint_for_auth_token_error(tmp_path):
    """Health.evaluate enthält die Hinweiszeile mit ./set-secret.sh bei Login-Fehlern."""
    db_path = tmp_path / "seen.sqlite"
    with Health(db_path) as h:
        h.record("sc_login", ok=False, found=0, error="SOUNDCLOUD_AUTH_TOKEN abgelehnt (401/403)")
        msg = h.evaluate("sc_login", 1)

    assert msg is not None
    assert "SoundCloud-Login abgelaufen" in msg
    assert "<code>./set-secret.sh</code>" in msg


def test_health_evaluate_no_auth_hint_for_other_errors(tmp_path):
    """Andere Fehler erzeugen keine SoundCloud-Login-Hinweiszeile."""
    db_path = tmp_path / "seen.sqlite"
    with Health(db_path) as h:
        h.record("discover", ok=False, found=0, error="ConnectionResetError: server dropped")
        msg = h.evaluate("discover", 1)

    assert msg is not None
    assert "SoundCloud-Login abgelaufen" not in msg


def test_guard_auth_token_when_get_client_id_fails(tmp_path, monkeypatch):
    """Fehlschlag bei get_client_id bricht den Lauf nicht ab und führt zu keiner Health-Meldung."""
    monkeypatch.setenv("SOUNDCLOUD_AUTH_TOKEN", "mein-token")
    cfg = _cfg(tmp_path)

    sc = MagicMock(spec=SoundCloudClient)
    sc.get_client_id.side_effect = RuntimeError("client_id nicht lesbar")

    check_mock = MagicMock()
    res = m.guard_auth_token(cfg, sc, no_telegram=True, check=check_mock)

    assert res is cfg
    assert check_mock.call_count == 0
    with Health(tmp_path / "seen.sqlite") as h:
        assert h.evaluate("sc_login", 1) is None


def test_check_auth_token_default_get_mocked():
    """check_auth_token ruft requests.get auf, wenn get None ist."""
    with patch("sc_digger.soundcloud.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        status = check_auth_token("mein-token", client_id="cid")
        assert status is TokenStatus.VALID
        mock_get.assert_called_once()
