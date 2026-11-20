from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger import mixesdb as mixesdb_mod
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-mixesdb"
AUTH = ("sc", PASSWORD)

WIKITEXT = """== Tracklist ==

<list>
[000] Robert Natus - Dead People [Frown - 04]
[008] ?
[048] Sven Wittekind - Sunstorm [Abstract - 4260186570223]
</list>
"""


def _client(tmp_path: Path, *, with_db: bool = True) -> TestClient:
    cfg = Config.load(ROOT / "config.yaml")
    db_path = tmp_path / "tracks.sqlite"
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(db_path),
    }
    if with_db:
        with TrackDB(db_path) as db:
            db.upsert_track(TrackRecord(
                path="/sammlung/Robert Natus - Dead People.flac",
                mtime=1.0, size=1, artist="Robert Natus", title="Dead People",
                status="archive",
            ))
    return TestClient(create_app(cfg, password=PASSWORD))


def test_akz_ohne_anmeldung_401(tmp_path: Path):
    response = _client(tmp_path).get("/mixesdb")
    assert response.status_code == 401


def test_akz_seite_zeigt_suchformular(tmp_path: Path):
    response = _client(tmp_path).get("/mixesdb", auth=AUTH)
    assert response.status_code == 200
    assert 'name="q"' in response.text


def test_akz_suche_listet_treffer_als_links(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mixesdb_mod, "search_sets", lambda q, limit=10: ["2006-03-03 - Natus @ Graz"])
    response = _client(tmp_path).get("/mixesdb", params={"q": "Natus"}, auth=AUTH)
    assert response.status_code == 200
    assert "?seite=2006-03-03+-+Natus+%40+Graz" in response.text or "seite=2006-03-03" in response.text


def test_akz_tracklist_zeigt_marken_und_zaehler(tmp_path: Path, monkeypatch):
    setlist = mixesdb_mod.parse_setlist(WIKITEXT, page="Natus")
    monkeypatch.setattr(mixesdb_mod, "fetch_setlist", lambda seite: setlist)
    response = _client(tmp_path).get("/mixesdb", params={"seite": "Natus"}, auth=AUTH)
    assert response.status_code == 200
    text = response.text
    assert "3 Einträge" in text and "1 fehlen" in text and "1 vorhanden" in text and "1 offen" in text
    assert "Dead People" in text and "Sunstorm" in text


def test_akz_unbekannte_seite_bleibt_freundlich(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mixesdb_mod, "fetch_setlist", lambda seite: None)
    response = _client(tmp_path).get("/mixesdb", params={"seite": "gibt es nicht"}, auth=AUTH)
    assert response.status_code == 200
    assert "nicht gefunden" in response.text


def test_akz_fehlende_db_ist_kein_fehler(tmp_path: Path, monkeypatch):
    setlist = mixesdb_mod.parse_setlist(WIKITEXT, page="Natus")
    monkeypatch.setattr(mixesdb_mod, "fetch_setlist", lambda seite: setlist)
    response = _client(tmp_path, with_db=False).get("/mixesdb", params={"seite": "Natus"}, auth=AUTH)
    assert response.status_code == 200
    assert "3 Einträge" in response.text and "0 vorhanden" in response.text


def test_akz_leere_tracklist_meldet_hinweis(tmp_path: Path, monkeypatch):
    leer = mixesdb_mod.parse_setlist("== Tracklist ==\n\n<list>\n\n</list>\n", page="Leer")
    monkeypatch.setattr(mixesdb_mod, "fetch_setlist", lambda seite: leer)
    response = _client(tmp_path).get("/mixesdb", params={"seite": "Leer"}, auth=AUTH)
    assert response.status_code == 200
    assert "leer" in response.text.lower()


def test_akz_mix_seite_verlinkt_die_setlisten(tmp_path: Path):
    response = _client(tmp_path).get("/mix", auth=AUTH)
    assert response.status_code == 200
    assert 'href="/mixesdb"' in response.text
