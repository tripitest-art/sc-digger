from pathlib import Path
from unittest.mock import patch

from sc_digger import bot as b
from sc_digger import mixesdb
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config

ROOT = Path(__file__).resolve().parents[2]

WIKITEXT = """== Tracklist ==

<list>
[000] Robert Natus - Dead People & More [Frown - 04]
[008] ?
[048] Sven Wittekind - Sunstorm [Abstract - 4260186570223]
</list>
"""


def _cfg(tmp_path: Path, *, with_track: bool = True) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    db_path = tmp_path / "tracks.sqlite"
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(db_path),
    }
    if with_track:
        with TrackDB(db_path) as db:
            db.upsert_track(TrackRecord(
                path="/sammlung/Robert Natus - Dead People & More.flac",
                mtime=1.0, size=1, artist="Robert Natus", title="Dead People & More",
                status="archive",
            ))
    return cfg


def test_akz_ohne_argument_zeigt_nutzung(tmp_path: Path):
    assert b.set_reply(_cfg(tmp_path), "/set") == b.SET_USAGE


def test_akz_suche_listet_treffer_und_merkt_sie(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mixesdb, "search_sets", lambda q, limit=5: ["2006 - Natus @ Graz", "2007 - Natus @ Bern"])
    antwort = b.set_reply(_cfg(tmp_path), "/set Natus", chat_id="akz-1")
    assert "2 Treffer" in antwort
    assert "1. 2006 - Natus @ Graz" in antwort and "2. 2007 - Natus @ Bern" in antwort
    assert "/set 1" in antwort
    assert b.SET_STATE["akz-1"] == ["2006 - Natus @ Graz", "2007 - Natus @ Bern"]


def test_akz_zahl_zeigt_nur_fehlende_tracks(tmp_path: Path, monkeypatch):
    setlist = mixesdb.parse_setlist(WIKITEXT, page="Natus")
    monkeypatch.setattr(mixesdb, "search_sets", lambda q, limit=5: ["Natus"])
    monkeypatch.setattr(mixesdb, "fetch_setlist", lambda seite: setlist)
    cfg = _cfg(tmp_path)
    b.set_reply(cfg, "/set Natus", chat_id="akz-2")
    antwort = b.set_reply(cfg, "/set 1", chat_id="akz-2")
    assert "3 Einträge" in antwort and "1 fehlen" in antwort and "1 vorhanden" in antwort
    assert "Sven Wittekind - Sunstorm [Abstract - 4260186570223]" in antwort
    # Vorhandenes wird nicht wieder aufgezählt, die offene Stelle nur gezählt.
    assert "Dead People" not in antwort
    assert "1 Stelle ist im Wiki nicht benannt." in antwort


def test_akz_maskiert_html(tmp_path: Path, monkeypatch):
    setlist = mixesdb.parse_setlist(WIKITEXT, page="Natus")
    monkeypatch.setattr(mixesdb, "search_sets", lambda q, limit=5: ["Natus"])
    monkeypatch.setattr(mixesdb, "fetch_setlist", lambda seite: setlist)
    # without_track: der Track mit "&" muss FEHLEN, sonst erscheint er nicht in der Antwort
    cfg = _cfg(tmp_path, with_track=False)
    b.set_reply(cfg, "/set Natus", chat_id="akz-3")
    antwort = b.set_reply(cfg, "/set 1", chat_id="akz-3")
    assert "&amp;" in antwort and " & " not in antwort


def test_akz_zahl_ohne_suche_und_ausserhalb(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mixesdb, "search_sets", lambda q, limit=5: ["Natus"])
    cfg = _cfg(tmp_path)
    assert b.set_reply(cfg, "/set 3", chat_id="akz-4").startswith("Keine Suche offen")
    b.set_reply(cfg, "/set Natus", chat_id="akz-4")
    assert "Nur 1 Treffer gemerkt" in b.set_reply(cfg, "/set 3", chat_id="akz-4")


def test_akz_seite_nicht_gefunden(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mixesdb, "search_sets", lambda q, limit=5: ["Natus"])
    monkeypatch.setattr(mixesdb, "fetch_setlist", lambda seite: None)
    cfg = _cfg(tmp_path)
    b.set_reply(cfg, "/set Natus", chat_id="akz-5")
    assert "nicht auf MixesDB gefunden" in b.set_reply(cfg, "/set 1", chat_id="akz-5")


def test_akz_keine_treffer(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mixesdb, "search_sets", lambda q, limit=5: [])
    antwort = b.set_reply(_cfg(tmp_path), "/set Nix", chat_id="akz-6")
    assert "Keine MixesDB-Seite zu" in antwort and "gefunden." in antwort


def test_akz_lange_liste_wird_gekuerzt(tmp_path: Path, monkeypatch):
    zeilen = "\n".join(
        f"[{i:03d}] Artist {i} - Titel {i} [Label - {i}]" for i in range(40)
    )
    setlist = mixesdb.parse_setlist("== Tracklist ==\n\n<list>\n" + zeilen + "\n</list>\n", page="Lang")
    monkeypatch.setattr(mixesdb, "search_sets", lambda q, limit=5: ["Lang"])
    monkeypatch.setattr(mixesdb, "fetch_setlist", lambda seite: setlist)
    cfg = _cfg(tmp_path, with_track=False)
    b.set_reply(cfg, "/set Lang", chat_id="akz-7")
    antwort = b.set_reply(cfg, "/set 1", chat_id="akz-7")
    assert "weitere fehlen (Liste gekürzt)" in antwort
    assert len(antwort) <= 3900


def test_akz_handle_message_sendet_die_antwort(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mixesdb, "search_sets", lambda q, limit=5: ["Natus"])
    gesendet = []
    with patch.object(b, "_send_text", side_effect=lambda c, chat, msg: gesendet.append((chat, msg))):
        b.handle_message(_cfg(tmp_path), None, "akz-8", "/SET@mein_bot Natus")
    assert gesendet and gesendet[0][0] == "akz-8"
    assert "1 Treffer" in gesendet[0][1]


def test_akz_help_nennt_den_befehl(tmp_path: Path):
    assert "/set" in b.HELP_TEXT
