"""Tests für die Suche im Konfigeditor (Issue #704)."""
import html as html_lib
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.schema import FIELDS
from sc_digger.web.app import config_sections, create_app

ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = ROOT / "config.yaml"
PASSWORD = "geheim-suche-test-4711"
AUTH = ("sc", PASSWORD)


@pytest.fixture(autouse=True)
def no_local_env(monkeypatch):
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)


def make_client(tmp_path):
    local_path = tmp_path / "config.local.yaml"
    local_path.write_text("", encoding="utf-8")
    cfg = Config.load(BASE_PATH, local=local_path)
    app = create_app(cfg, password=PASSWORD, base_path=BASE_PATH, local_path=local_path)
    return TestClient(app)


def test_suchfeld_und_leerzustand_vorhanden(tmp_path):
    client = make_client(tmp_path)
    html = client.get("/config", auth=AUTH).text
    assert 'id="config-suche"' in html
    assert "Feld suchen" in html
    assert '<p id="config-keine-treffer" hidden' in html


def test_ohne_javascript_nichts_im_markup_versteckt(tmp_path):
    client = make_client(tmp_path)
    html = client.get("/config", auth=AUTH).text
    for div in re.findall(r'<div class="field"[^>]*>', html):
        assert " hidden" not in div


def test_jedes_editierbare_feld_traegt_suchtext_mit_punktpfad(tmp_path):
    client = make_client(tmp_path)
    editable_fields = {f.path: f for f in FIELDS if f.editable}
    assert len(editable_fields) > 0

    found_paths = set()
    for section in config_sections():
        html = client.get(f"/config/{section.slug}", auth=AUTH).text
        # Teile an <div class="field"
        blocks = html.split('<div class="field"')[1:]
        for block in blocks:
            id_match = re.search(r'id="f-([^"]+)"', block)
            if not id_match:
                continue
            fpath = id_match.group(1)
            field = editable_fields.get(fpath)
            assert field is not None, f"Unerwartetes Feld: {fpath}"

            suchtext_match = re.search(r'data-suchtext="([^"]*)"', block[:1000])
            assert suchtext_match is not None, f"Feld {fpath} hat kein data-suchtext"
            suchtext = html_lib.unescape(suchtext_match.group(1))

            assert fpath.lower() in suchtext, f"Pfad {fpath} fehlt im Suchtext"
            if field.label:
                assert field.label.lower() in suchtext, f"Label {field.label} fehlt im Suchtext von {fpath}"
            if field.help:
                assert field.help.lower() in suchtext, f"Hilfetext fehlt im Suchtext von {fpath}"
            found_paths.add(fpath)

    assert found_paths == set(editable_fields.keys())
