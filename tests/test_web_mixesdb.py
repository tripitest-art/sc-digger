"""Tests für die MixesDB-Seite im Web-Dashboard (Issue #651)."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger import mixesdb as mixesdb_mod
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.routes.mixesdb import MAX_TREFFER, _seite_from_query, collection_names

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-651"
AUTH = ("sc", PASSWORD)

WIKITEXT = """== Tracklist ==

<list>
[000] Robert Natus - Dead People [Frown - 04]
[008] ?
[048] Sven Wittekind - Sunstorm [Abstract - 4260186570223]
</list>
"""


def _track(db_path: Path, *, artist: str, title: str, status: str = "archive") -> None:
    with TrackDB(db_path) as db:
        db.upsert_track(TrackRecord(
            path=f"/sammlung/{artist} - {title}.flac",
            mtime=1.0,
            size=1,
            artist=artist,
            title=title,
            status=status,
        ))


def _client(tmp_path: Path, *, with_db: bool = True) -> TestClient:
    cfg = Config.load(ROOT / "config.yaml")
    db_path = tmp_path / "tracks.sqlite"
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(db_path),
    }
    if with_db:
        _track(db_path, artist="Robert Natus", title="Dead People")
    return TestClient(create_app(cfg, password=PASSWORD))


# ------------------------------------------------------------------ Hilfsfunktionen


def test_collection_names_liest_archiv_und_adopted(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    _track(db_path, artist="A", title="Eins")
    _track(db_path, artist="B", title="Zwei", status="adopted")
    _track(db_path, artist="C", title="Inbox", status="inbox")

    names = collection_names(db_path)

    assert ("A", "Eins") in names
    assert ("B", "Zwei") in names
    assert ("C", "Inbox") not in names


def test_collection_names_fehlende_datei_ist_leer(tmp_path: Path):
    db_path = tmp_path / "fehlt.sqlite"

    assert collection_names(db_path) == []
    assert not db_path.exists()


def test_collection_names_fehlende_tabelle_ist_leer(tmp_path: Path):
    db_path = tmp_path / "leer.sqlite"
    db_path.write_bytes(b"")

    assert collection_names(db_path) == []


def test_seite_from_query_nimmt_titel_und_url():
    assert _seite_from_query("2006-03-03 - Natus @ Graz") == "2006-03-03 - Natus @ Graz"
    assert (
        _seite_from_query("https://www.mixesdb.com/w/2006-03-03_-_Natus_@_Graz")
        == "2006-03-03 - Natus @ Graz"
    )
    assert _seite_from_query("  Titel  ") == "Titel"


# ------------------------------------------------------------------ Seite


def test_seite_ohne_parameter_zeigt_formular(tmp_path: Path):
    response = _client(tmp_path).get("/mixesdb", auth=AUTH)

    assert response.status_code == 200
    assert 'action="/mixesdb"' in response.text
    assert 'name="q"' in response.text


def test_suche_zeigt_treffer_als_kodierte_links(tmp_path: Path, monkeypatch):
    gesehen: list[tuple[str, int]] = []

    def fake(query: str, *, limit: int = 10):
        gesehen.append((query, limit))
        return ["2006-03-03 - Natus @ Graz"]

    monkeypatch.setattr(mixesdb_mod, "search_sets", fake)

    response = _client(tmp_path).get("/mixesdb", params={"q": "Natus"}, auth=AUTH)

    assert response.status_code == 200
    assert gesehen == [("Natus", MAX_TREFFER)]
    assert "?seite=2006-03-03+-+Natus+%40+Graz" in response.text


def test_suche_ohne_treffer_meldet_hinweis(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mixesdb_mod, "search_sets", lambda q, limit=10: [])

    response = _client(tmp_path).get("/mixesdb", params={"q": "Nix"}, auth=AUTH)

    assert response.status_code == 200
    assert "Keine MixesDB-Seite" in response.text


def test_tracklist_zeigt_marken_positionen_und_zaehler(tmp_path: Path, monkeypatch):
    setlist = mixesdb_mod.parse_setlist(WIKITEXT, page="Natus")
    monkeypatch.setattr(mixesdb_mod, "fetch_setlist", lambda seite: setlist)

    response = _client(tmp_path).get("/mixesdb", params={"seite": "Natus"}, auth=AUTH)

    text = response.text
    assert response.status_code == 200
    assert ">3<" not in text  # Zahlen stehen im <strong>, nicht nackt im Text
    assert "Einträge" in text and "fehlen" in text and "vorhanden" in text and "offen" in text
    assert "Dead People" in text and "Sunstorm" in text
    assert "48:00" in text and "0:00" in text
    assert "Abstract" in text and "4260186570223" in text
    assert "is-here" in text and "is-missing" in text and "is-open" in text
    assert "offene Stelle" in text


def test_seite_gewinnt_gegen_suchbegriff(tmp_path: Path, monkeypatch):
    setlist = mixesdb_mod.parse_setlist(WIKITEXT, page="Natus")
    monkeypatch.setattr(mixesdb_mod, "fetch_setlist", lambda seite: setlist)
    monkeypatch.setattr(
        mixesdb_mod, "search_sets", lambda q, limit=10: ["sollte nicht erscheinen"]
    )

    response = _client(tmp_path).get(
        "/mixesdb", params={"q": "Natus", "seite": "Natus"}, auth=AUTH
    )

    assert response.status_code == 200
    assert "sollte nicht erscheinen" not in response.text
    assert "Dead People" in response.text


def test_unbekannte_seite_meldet_hinweis(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mixesdb_mod, "fetch_setlist", lambda seite: None)

    response = _client(tmp_path).get("/mixesdb", params={"seite": "gibt es nicht"}, auth=AUTH)

    assert response.status_code == 200
    assert "nicht gefunden" in response.text


def test_leere_tracklist_meldet_hinweis(tmp_path: Path, monkeypatch):
    leer = mixesdb_mod.parse_setlist("== Tracklist ==\n\n<list>\n\n</list>\n", page="Leer")
    monkeypatch.setattr(mixesdb_mod, "fetch_setlist", lambda seite: leer)

    response = _client(tmp_path).get("/mixesdb", params={"seite": "Leer"}, auth=AUTH)

    assert response.status_code == 200
    assert "Wiki-Tracklist ist leer" in response.text


def test_fehlende_db_zaehlt_alles_als_fehlend(tmp_path: Path, monkeypatch):
    setlist = mixesdb_mod.parse_setlist(WIKITEXT, page="Natus")
    monkeypatch.setattr(mixesdb_mod, "fetch_setlist", lambda seite: setlist)

    response = _client(tmp_path, with_db=False).get(
        "/mixesdb", params={"seite": "Natus"}, auth=AUTH
    )

    assert response.status_code == 200
    assert "Dead People" in response.text
    # Ohne Sammlung darf keine Zeile als vorhanden gelten (die CSS-Klasse steht im <style>).
    assert response.text.count('class="mixesdb-mark is-here"') == 0
    assert response.text.count('class="mixesdb-mark is-missing"') == 2


def test_ohne_anmeldung_kein_zugriff(tmp_path: Path):
    assert _client(tmp_path).get("/mixesdb").status_code == 401


def test_mix_seite_verlinkt_die_setlisten(tmp_path: Path):
    """Einstieg ohne Nav-Änderung: die Bereichsliste aus #600 bleibt unangetastet."""
    response = _client(tmp_path).get("/mix", auth=AUTH)

    assert response.status_code == 200
    assert 'href="/mixesdb"' in response.text
