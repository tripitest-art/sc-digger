"""Akzeptanztests: Boolean-Felder sind Schalter, fehlendes Feld heisst aus."""

import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
BASE_PATH = ROOT / "config.yaml"
PASSWORD = "geheim-4711"
AUTH = ("sc", PASSWORD)
ORIGIN = {"Origin": "http://testserver"}
SEITE = "/config/sortieren-taggen"


@pytest.fixture(autouse=True)
def ohne_lokale_datei(monkeypatch):
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)


def _client(tmp_path, local: dict | None = None):
    local_path = tmp_path / "config.local.yaml"
    if local is not None:
        local_path.write_text(yaml.safe_dump(local), encoding="utf-8")
    cfg = Config.load(BASE_PATH, local=local_path)
    app = create_app(cfg, password=PASSWORD, base_path=BASE_PATH, local_path=local_path)
    return TestClient(app), local_path


def test_bool_feld_ist_schalter_statt_auswahlliste(tmp_path):
    client, _ = _client(tmp_path)
    html = client.get(SEITE, auth=AUTH).text
    assert 'role="switch"' in html
    assert not re.search(r'<option value="true"', html), "Boolean-Felder rendern weiter als Auswahlliste"


def test_fehlendes_bool_wird_als_aus_gespeichert(tmp_path):
    # Checkbox nicht angehakt = Feld fehlt im Formular = aus.
    client, local_path = _client(tmp_path)
    antwort = client.post("/config/save", data={"_section": "sortieren-taggen"},
                          auth=AUTH, headers=ORIGIN, follow_redirects=False)
    assert antwort.status_code == 303
    gespeichert = yaml.safe_load(local_path.read_text(encoding="utf-8"))
    assert gespeichert["organize"]["detect_bpm"] is False
    assert gespeichert["organize"]["detect_key"] is False


def test_gesetzter_wert_wird_angewendet(tmp_path):
    # Kontrolltest: laeuft schon heute gruen. detect_bpm steht in config.yaml auf true,
    # der gepostete false erzeugt also einen Override.
    client, local_path = _client(tmp_path)
    antwort = client.post(
        "/config/save",
        data={"organize.detect_bpm": "false", "_section": "sortieren-taggen"},
        auth=AUTH, headers=ORIGIN, follow_redirects=False,
    )
    assert antwort.status_code == 303
    gespeichert = yaml.safe_load(local_path.read_text(encoding="utf-8"))
    assert gespeichert["organize"]["detect_bpm"] is False


def test_schalter_zeigt_aus_zustand(tmp_path):
    client, _ = _client(tmp_path, local={"organize": {"enabled": False}})
    html = client.get(SEITE, auth=AUTH).text
    treffer = re.search(r'<input[^>]*id="f-organize\\.enabled"[^>]*>', html)
    assert treffer, "kein Schalter-Element fuer organize.enabled"
    tag = treffer.group(0)
    assert 'role="switch"' in tag
    assert "checked" not in tag, "ausgeschalteter Wert darf nicht als checked erscheinen"
