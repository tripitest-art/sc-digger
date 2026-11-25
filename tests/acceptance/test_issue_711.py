"""Akzeptanztests: Hilfe-Seite „So funktioniert sc-digger“ (Issue <N>)."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.glossary import GLOSSAR
from sc_digger.web.hilfe import STATIONEN, TAG_SCHRITTE

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-hilfe"


def _client(tmp_path: Path) -> TestClient:
    cfg = Config.load(ROOT / "config.yaml", local=tmp_path / "keine-override.yaml")
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    cfg.raw.setdefault("download", {})["inbox_dir"] = str(inbox)
    return TestClient(create_app(cfg, password=PASSWORD))


def test_stationen_in_fester_reihenfolge():
    assert [(s.key, s.href) for s in STATIONEN] == [
        ("entdecken", "/digest"), ("bewerten", "/digest/durchhoeren"), ("laden", "/intake"),
        ("pruefen", "/rejected"), ("inbox", "/tracks"), ("rekordbox", "/"), ("sammlung", "/audit"),
    ]
    assert all(s.titel and s.text and s.ziel for s in STATIONEN)
    assert len(TAG_SCHRITTE) == 3
    assert [href for _, href, _ in TAG_SCHRITTE] == ["/digest/durchhoeren", "/intake", "/tracks"]


def test_texte_halten_das_glossar_ein():
    verboten = {w.lower() for _, (_, nicht) in GLOSSAR.items() for w in nicht if w != "Eingang"}
    for s in STATIONEN:
        woerter = {w.strip(".,;:–()").lower() for w in s.text.split()}
        assert not (woerter & verboten), (s.key, woerter & verboten)


def test_hilfe_seite_zeigt_den_weg_und_den_tag(tmp_path):
    client = _client(tmp_path)
    assert client.get("/hilfe").status_code == 401
    resp = client.get("/hilfe", auth=("sc", PASSWORD))
    assert resp.status_code == 200
    html = resp.text
    assert "So funktioniert sc-digger" in html
    positionen = [html.index(f'data-station="{s.key}"') for s in STATIONEN]
    assert positionen == sorted(positionen)
    for s in STATIONEN:
        assert s.titel in html and f'href="{s.href}"' in html
    for nr in (1, 2, 3):
        assert f'data-schritt="{nr}"' in html
    assert 'href="/glossar"' in html


def test_alle_ziele_der_hilfe_sind_erreichbar(tmp_path):
    client = _client(tmp_path)
    for href in {s.href for s in STATIONEN} | {h for _, h, _ in TAG_SCHRITTE}:
        assert client.get(href, auth=("sc", PASSWORD)).status_code == 200, href


def test_kopf_jeder_seite_verlinkt_die_hilfe(tmp_path):
    client = _client(tmp_path)
    for seite in ("/", "/glossar", "/hilfe"):
        assert 'href="/hilfe"' in client.get(seite, auth=("sc", PASSWORD)).text, seite
