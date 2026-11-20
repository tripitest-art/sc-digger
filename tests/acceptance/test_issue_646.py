"""Akzeptanztests: Zeilen der vier Listen verlinken auf die Track-Detailseite (Teil 2b von #590, Issue #<N>)."""
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.web import app as web_app

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-tl-4711"
AUTH = ("sc", PASSWORD)
SC = 4712
TEMPLATES = ROOT / "sc_digger" / "web" / "templates"


def _client(tmp_path):
    track_db = tmp_path / "track.sqlite"
    with TrackDB(track_db) as db:
        db.upsert_store_item(SC, "Titel 2", "Artist 2", "https://store.example/y")
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(track_db),
    }
    return TestClient(web_app.create_app(cfg, password=PASSWORD))


def test_kaufliste_verlinkt_auf_die_detailseite(tmp_path):
    html = _client(tmp_path).get("/kaufliste", auth=AUTH).text
    assert f'href="/track/sc/{SC}"' in html
    assert 'class="track-link"' in html


def test_alle_vier_templates_tragen_den_link():
    """Formprüfung: Die vier Listen müssen den Link als Markup enthalten.

    Die Seiten Digest, Track-Explorer und Quarantäne brauchen für einen Rendering-Test
    eine gefüllte Datenbank; die Form des Markups lässt sich davon unabhängig prüfen.
    """
    for name in ("digest.html", "tracks.html", "rejected.html", "kaufliste.html"):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        assert 'class="track-link"' in text, f"{name}: kein track-link"
        assert 'href="/track/sc/{{' in text, f"{name}: kein Link auf /track/sc/"


def test_kein_link_ohne_sc_id():
    """Ohne sc_id darf kein kaputtes Ziel entstehen — die Zelle bleibt dann Text."""
    for name in ("digest.html", "tracks.html", "rejected.html", "kaufliste.html"):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "/track/sc/None" not in text, f"{name}: Link auf /track/sc/None"
        assert "/track/sc/{{ item.sc_id }}" in text or "/track/sc/{{ track.sc_id }}" in text, \
            f"{name}: sc_id wird nicht als Linkziel verwendet"
