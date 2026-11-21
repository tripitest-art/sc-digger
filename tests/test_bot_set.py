"""Eigene Tests für den Bot-Befehl /set (MixesDB-Setlisten und Tracklist-Abgleich, Issue #682)."""
from pathlib import Path
from unittest.mock import patch
import pytest

from sc_digger import bot as b
from sc_digger import mixesdb
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config

ROOT = Path(__file__).resolve().parents[1]

WIKITEXT_SAMPLE = """== Tracklist ==

<list>
[000] Robert Natus - Dead People [Frown - 04]
[008] ?
[015] Arkus P - Habsucht [Schlachthof]
[048] Sven Wittekind - Sunstorm [Abstract - 4260186570223]
</list>
"""


def _make_cfg(tmp_path: Path, tracks: list[tuple[str, str]] | None = None) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    db_path = tmp_path / "tracks.sqlite"
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(db_path),
    }
    if tracks is not None:
        with TrackDB(db_path) as db:
            for artist, title in tracks:
                db.upsert_track(
                    TrackRecord(
                        path=f"/sammlung/{artist} - {title}.flac",
                        mtime=1.0,
                        size=1,
                        artist=artist,
                        title=title,
                        status="archive",
                    )
                )
    return cfg


def test_set_reply_alle_tracks_vorhanden(tmp_path: Path, monkeypatch):
    """Wenn alle Tracks vorhanden sind, erscheint 'Alle X Einträge liegen in der Sammlung.'"""
    setlist = mixesdb.parse_setlist(
        "== Tracklist ==\n<list>\n[000] A - Eins\n[005] B - Zwei\n</list>\n", page="Voll"
    )
    monkeypatch.setattr(mixesdb, "search_sets", lambda q, limit=5: ["Voll"])
    monkeypatch.setattr(mixesdb, "fetch_setlist", lambda seite: setlist)
    cfg = _make_cfg(tmp_path, [("A", "Eins"), ("B", "Zwei")])

    b.set_reply(cfg, "/set Voll", chat_id="chat-full")
    reply = b.set_reply(cfg, "/set 1", chat_id="chat-full")

    assert "Alle 2 Einträge liegen in der Sammlung." in reply
    assert "0 fehlen" in reply


def test_set_reply_mehrere_offene_stellen(tmp_path: Path, monkeypatch):
    """Bei mehreren offenen Stellen wird der Plural 'X Stellen sind im Wiki nicht benannt.' verwendet."""
    setlist = mixesdb.parse_setlist(
        "== Tracklist ==\n<list>\n[000] A - Eins\n[005] ?\n[010] ?\n</list>\n", page="Offen"
    )
    monkeypatch.setattr(mixesdb, "search_sets", lambda q, limit=5: ["Offen"])
    monkeypatch.setattr(mixesdb, "fetch_setlist", lambda seite: setlist)
    cfg = _make_cfg(tmp_path, [("A", "Eins")])

    b.set_reply(cfg, "/set Offen", chat_id="chat-open")
    reply = b.set_reply(cfg, "/set 1", chat_id="chat-open")

    assert "2 Stellen sind im Wiki nicht benannt." in reply


def test_set_reply_eine_offene_stelle(tmp_path: Path, monkeypatch):
    """Bei genau einer offenen Stelle wird die Einzahl '1 Stelle ist im Wiki nicht benannt.' verwendet."""
    setlist = mixesdb.parse_setlist(
        "== Tracklist ==\n<list>\n[000] A - Eins\n[005] ?\n</list>\n", page="EineStelle"
    )
    monkeypatch.setattr(mixesdb, "search_sets", lambda q, limit=5: ["EineStelle"])
    monkeypatch.setattr(mixesdb, "fetch_setlist", lambda seite: setlist)
    cfg = _make_cfg(tmp_path, [("A", "Eins")])

    b.set_reply(cfg, "/set EineStelle", chat_id="chat-one")
    reply = b.set_reply(cfg, "/set 1", chat_id="chat-one")

    assert "1 Stelle ist im Wiki nicht benannt." in reply


def test_set_reply_katalog_ohne_label_und_label_ohne_katalog(tmp_path: Path, monkeypatch):
    """Prüft Formatierung von Label ohne Katalognummer in fehlenden Tracks."""
    setlist = mixesdb.parse_setlist(WIKITEXT_SAMPLE, page="Sample")
    monkeypatch.setattr(mixesdb, "search_sets", lambda q, limit=5: ["Sample"])
    monkeypatch.setattr(mixesdb, "fetch_setlist", lambda seite: setlist)
    cfg = _make_cfg(tmp_path, [("Robert Natus", "Dead People")])

    b.set_reply(cfg, "/set Sample", chat_id="chat-fmt")
    reply = b.set_reply(cfg, "/set 1", chat_id="chat-fmt")

    # Arkus P hat Label [Schlachthof] ohne Katalog
    assert "Arkus P - Habsucht [Schlachthof]" in reply
    # Sven Wittekind hat Label und Katalog
    assert "Sven Wittekind - Sunstorm [Abstract - 4260186570223]" in reply


def test_set_reply_nummer_grenzwerte(tmp_path: Path, monkeypatch):
    """Prüft Eingabe 0 und negative Zahlen sowie Zahlen größer als Trefferanzahl."""
    monkeypatch.setattr(mixesdb, "search_sets", lambda q, limit=5: ["Set A", "Set B"])
    cfg = _make_cfg(tmp_path)

    b.set_reply(cfg, "/set Suchtext", chat_id="chat-bounds")
    assert "Nur 2 Treffer gemerkt" in b.set_reply(cfg, "/set 0", chat_id="chat-bounds")
    assert "Nur 2 Treffer gemerkt" in b.set_reply(cfg, "/set 3", chat_id="chat-bounds")


def test_handle_message_exception_sendet_fehlermeldung(tmp_path: Path, monkeypatch):
    """Wenn set_reply eine Exception wirft, fängt handle_message sie ab."""
    def boom(*args, **kwargs):
        raise RuntimeError("MixesDB kaputt")

    monkeypatch.setattr(mixesdb, "search_sets", boom)
    gesendet = []
    cfg = _make_cfg(tmp_path)
    with patch.object(b, "_send_text", side_effect=lambda c, chat, msg: gesendet.append((chat, msg))):
        b.handle_message(cfg, None, "chat-err", "/set Test")

    assert gesendet and gesendet[0][0] == "chat-err"
    assert "Set-Abfrage fehlgeschlagen, siehe Container-Log." in gesendet[0][1]
