"""Eigene Tests für einheitliche Seitentitel im Web-Dashboard (Issue #491)."""
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.schema import FIELDS
from sc_digger.web.app import create_app
from sc_digger.web.configedit import format_form_value, get_path
from tests.test_web_tracks_ui import PASSWORD, client_for

ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = ROOT / "config.yaml"
AUTH = ("sc", PASSWORD)


@pytest.fixture(autouse=True)
def no_local_env(monkeypatch):
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)


def _extract_title(html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, re.S)
    assert match is not None, "Kein <title>-Tag in HTML gefunden"
    return match.group(1).strip()


@pytest.mark.parametrize(
    ("pfad", "erwarteter_titel"),
    [
        ("/", "Status – sc-digger"),
        ("/tracks", "Tracks – sc-digger"),
        ("/digest", "Digest &amp; Entdeckungen – sc-digger"),
        ("/mix", "Mix – sc-digger"),
        ("/kaufliste", "Kaufliste – sc-digger"),
        ("/rejected", "Quarantäne – sc-digger"),
        ("/duplicates", "Duplikat-Kandidaten – sc-digger"),
        ("/intake", "Upload &amp; Intake – sc-digger"),
    ],
)
def test_standard_seiten_titel_format_und_inhalt(tmp_path, pfad, erwarteter_titel):
    client = client_for(tmp_path)
    r = client.get(pfad, auth=AUTH)
    assert r.status_code == 200
    titel = _extract_title(r.text)
    assert titel == erwarteter_titel
    assert titel.endswith(" – sc-digger")
    assert not titel.startswith("sc-digger")
    # Gedankenstrich U+2013 prüfen
    assert " \u2013 " in titel
    assert " - " not in titel


def _make_config_client(tmp_path):
    local_path = tmp_path / "config.local.yaml"
    local_path.write_text("{}", encoding="utf-8")
    cfg = Config.load(BASE_PATH, local=local_path)
    app = create_app(cfg, password=PASSWORD, base_path=BASE_PATH, local_path=local_path)
    return TestClient(app)


def test_config_seite_titel(tmp_path):
    client = _make_config_client(tmp_path)
    r = client.get("/config", auth=AUTH)
    assert r.status_code == 200
    titel = _extract_title(r.text)
    assert titel == "Konfiguration – sc-digger"
    assert titel.endswith(" – sc-digger")
    assert not titel.startswith("sc-digger")
    assert " \u2013 " in titel
    assert " - " not in titel


def test_config_preview_seite_titel(tmp_path):
    client = _make_config_client(tmp_path)
    raw = yaml.safe_load(BASE_PATH.read_text(encoding="utf-8"))
    form = {f.path: format_form_value(f, get_path(raw, f.path)) for f in FIELDS if f.editable}
    r = client.post(
        "/config/preview",
        auth=AUTH,
        data=form,
        headers={"Referer": "http://testserver/config"},
    )
    assert r.status_code == 200
    titel = _extract_title(r.text)
    assert titel == "Konfiguration: Vorschau – sc-digger"
    assert titel.endswith(" – sc-digger")
    assert not titel.startswith("sc-digger")
    assert " \u2013 " in titel
    assert " - " not in titel
