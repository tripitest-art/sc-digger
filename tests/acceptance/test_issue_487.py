"""Akzeptanztests: Filter-Chips im Track-Explorer."""
from sc_digger.web.app import active_filter_chips
from tests.test_web_tracks_ui import PASSWORD, client_for

LEER = {"q": "", "status": "", "bpm_min": ""}


def test_leere_filter_ergeben_keine_chips():
    assert active_filter_chips(LEER, limit=50) == []


def test_chip_je_gesetztem_filter_mit_label_und_wert():
    chips = active_filter_chips({"q": "svetec", "status": "inbox", "bpm_min": ""}, limit=50)
    assert [(c["key"], c["label"], c["value"]) for c in chips] == [
        ("q", "Suche", "svetec"), ("status", "Status", "inbox")]


def test_remove_url_ohne_diesen_filter_aber_mit_rest_limit_und_sortierung():
    chips = active_filter_chips({"q": "svetec", "status": "inbox"}, limit=25, sort="bpm", direction="desc")
    pfad, _, rest = chips[0]["remove_url"].partition("?")
    assert pfad == "/tracks"
    abfrage = dict(teil.split("=", 1) for teil in rest.split("&"))
    assert "q" not in abfrage
    assert abfrage["status"] == "inbox"
    assert abfrage["limit"] == "25"
    assert abfrage["sort"] == "bpm" and abfrage["dir"] == "desc"
    assert "offset" not in abfrage


def test_seite_zeigt_chip_leiste(tmp_path):
    r = client_for(tmp_path).get("/tracks?q=svetec&status=inbox", auth=("sc", PASSWORD))
    assert r.status_code == 200
    assert 'class="filter-chip"' in r.text
    assert "Suche: svetec" in r.text


def test_seite_ohne_filter_ohne_chips(tmp_path):
    r = client_for(tmp_path).get("/tracks", auth=("sc", PASSWORD))
    assert 'class="filter-chip"' not in r.text