"""Eigene Tests der Hilfe-Seite (Issue #711)."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.hilfe import STATIONEN, TAG_SCHRITTE

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-hilfe-eigen"


def _client(tmp_path: Path) -> TestClient:
    cfg = Config.load(ROOT / "config.yaml", local=tmp_path / "keine-override.yaml")
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    return TestClient(create_app(cfg, password=PASSWORD))


def test_stationen_keys_sind_eindeutig():
    keys = [s.key for s in STATIONEN]
    assert len(keys) == len(set(keys)) == 7


def test_station_ist_unveraenderlich():
    import dataclasses
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        STATIONEN[0].titel = "x"  # type: ignore[misc]


def test_seite_zeigt_text_und_ziel_jeder_station(tmp_path):
    html = _client(tmp_path).get("/hilfe", auth=("sc", PASSWORD)).text
    for s in STATIONEN:
        assert s.ziel in html
        # Der Text enthält Sonderzeichen (z. B. −), die Jinja unverändert ausgibt.
        assert s.text.split(".")[0] in html


def test_tagesschritte_stehen_in_reihenfolge(tmp_path):
    html = _client(tmp_path).get("/hilfe", auth=("sc", PASSWORD)).text
    pos = [html.index(text) for text, _, _ in TAG_SCHRITTE]
    assert pos == sorted(pos)
    assert "Dein Tag in 3 Schritten" in html


def test_hilfe_link_steht_vor_dem_glossar_link(tmp_path):
    html = _client(tmp_path).get("/glossar", auth=("sc", PASSWORD)).text
    assert html.index('href="/hilfe"') < html.index('href="/glossar" class="glossar-link"')
