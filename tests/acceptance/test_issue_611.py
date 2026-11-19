"""Akzeptanztests: Konfigeditor – wichtige Felder und „Wirkt auf“."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.schema import FIELDS, IMPORTANT_FIELDS, USED_BY, used_by
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
BASE_PATH = ROOT / "config.yaml"
PASSWORD = "geheim-konfig"
AUTH = ("sc", PASSWORD)


def _client(tmp_path):
    local_path = tmp_path / "config.local.yaml"
    cfg = Config.load(BASE_PATH, local=local_path)
    return TestClient(create_app(cfg, password=PASSWORD, base_path=BASE_PATH, local_path=local_path))


def test_wichtige_felder_sind_bearbeitbare_schemafelder():
    bearbeitbar = {f.path for f in FIELDS if f.editable}
    assert len(IMPORTANT_FIELDS) == 23
    assert IMPORTANT_FIELDS <= bearbeitbar
    assert {"search.tags", "loudness.target_lufs", "rekordbox.xml_enabled"} <= IMPORTANT_FIELDS


def test_jedes_bearbeitbare_feld_hat_wirkt_auf():
    for feld in FIELDS:
        if feld.editable:
            assert feld.used_by, feld.path
            assert feld.used_by == used_by(feld.path)
            assert feld.important == (feld.path in IMPORTANT_FIELDS)


def test_used_by_nimmt_den_laengsten_praefix():
    assert used_by("search.tags") == ("Discover-Lauf", "Playlist- und Similar-Modus")
    assert used_by("scoring.weights.velocity") == ("Discover-Lauf",)
    assert used_by("scout.bandcamp.feeds") == ("Store-Scout",)
    assert used_by("rekordbox") == USED_BY["rekordbox"]
    assert used_by("searchx.tags") == ()
    assert used_by("") == ()


def test_abschnittsseite_zeigt_umschaltung_und_wirkt_auf(tmp_path):
    html = _client(tmp_path).get("/config/suche", auth=AUTH).text
    assert 'class="config-view-toggle"' in html
    assert re.search(r'<button type="button" data-view="important" aria-pressed="true">Wichtig</button>', html)
    assert re.search(r'<button type="button" data-view="all" aria-pressed="false">Alle</button>', html)
    assert "Wirkt auf: Discover-Lauf, Playlist- und Similar-Modus" in html
    felder = re.findall(r'<div class="field" data-important="(true|false)" data-local="(true|false)"', html)
    assert ("true", "false") in felder and ("false", "false") in felder


def test_abschnitt_ohne_wichtige_felder_hat_keine_umschaltung(tmp_path):
    html = _client(tmp_path).get("/config/curator-mining", auth=AUTH).text
    assert 'data-important="false"' in html
    assert 'class="config-view-toggle"' not in html
    assert 'data-important="true"' not in html


def test_ohne_skript_ist_nichts_versteckt(tmp_path):
    html = _client(tmp_path).get("/config/suche", auth=AUTH).text
    for div in re.findall(r'<div class="field"[^>]*>', html):
        assert " hidden" not in div
