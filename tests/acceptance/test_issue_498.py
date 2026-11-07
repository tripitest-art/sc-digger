"""Akzeptanztests: Matcher-Korrektur per Button."""
from sc_digger.db import TrackDB
from sc_digger.web.database import save_match_override
from tests.test_web_tracks_ui import PASSWORD, client_for

ORIGIN = {"origin": "http://testserver"}


def _anlegen(tmp_path):
    pfad = tmp_path / "tracks.sqlite"
    with TrackDB(pfad):
        pass
    return pfad


def _holen(pfad, sc_id):
    with TrackDB(pfad) as db:
        return db.get_match_override(sc_id)


def test_speichern_ueberschreiben_und_loeschen(tmp_path):
    pfad = _anlegen(tmp_path)
    assert save_match_override(pfad, 7, "already_in_collection") is True
    assert _holen(pfad, 7) == "already_in_collection"
    assert save_match_override(pfad, 7, "not_duplicate") is True
    assert _holen(pfad, 7) == "not_duplicate"
    assert save_match_override(pfad, 7, "clear") is True
    assert _holen(pfad, 7) is None
    assert save_match_override(pfad, 99, "clear") is True


def test_ungueltige_eingaben_schreiben_nichts(tmp_path):
    pfad = _anlegen(tmp_path)
    assert save_match_override(pfad, 7, "kaputt") is False
    assert save_match_override(pfad, "abc", "not_duplicate") is False
    assert _holen(pfad, 7) is None


def test_fehlende_datenbank_wird_nicht_angelegt(tmp_path):
    pfad = tmp_path / "gibt-es-nicht.sqlite"
    assert save_match_override(pfad, 7, "not_duplicate") is False
    assert not pfad.exists()


def test_route_speichert(tmp_path):
    pfad = _anlegen(tmp_path)
    r = client_for(tmp_path).post("/api/match-override", json={"sc_id": 7, "override_type": "not_duplicate"},
                                  auth=("sc", PASSWORD), headers=ORIGIN)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "sc_id": 7, "override_type": "not_duplicate"}
    assert _holen(pfad, 7) == "not_duplicate"


def test_route_schutz_und_validierung(tmp_path):
    _anlegen(tmp_path)
    client = client_for(tmp_path)
    body = {"sc_id": 7, "override_type": "not_duplicate"}
    assert client.post("/api/match-override", json=body, headers=ORIGIN).status_code == 401
    assert client.post("/api/match-override", json=body, auth=("sc", PASSWORD)).status_code == 403
    assert client.post("/api/match-override", json=body, auth=("sc", PASSWORD),
                       headers={"origin": "https://boese.example"}).status_code == 403
    for schlecht in ({"sc_id": 7, "override_type": "x"}, {"sc_id": True, "override_type": "clear"}, {"override_type": "clear"}):
        r = client.post("/api/match-override", json=schlecht, auth=("sc", PASSWORD), headers=ORIGIN)
        assert r.status_code == 400