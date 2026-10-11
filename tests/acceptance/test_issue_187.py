"""Akzeptanztests: Bot-Befehl /sync_xml schreibt die Rekordbox-XML sofort neu."""
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from sc_digger import bot as b
from sc_digger.models import Config
from sc_digger.organize import write_tags

ROOT = Path(__file__).resolve().parents[2]
JETZT = datetime(2026, 10, 11, 17, 30)


def _audio(path: Path, **tags) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "anoisesrc=d=1:c=pink:r=44100:a=0.2", "-ac", "2", "-b:a", "320k", str(path)],
        check=True,
    )
    write_tags(path, **tags)
    return path


def _cfg(tmp_path, *, enabled=True, xml_in_collection=False):
    inbox = tmp_path / "inbox"
    coll = tmp_path / "coll"
    inbox.mkdir()
    coll.mkdir()
    xml = (coll if xml_in_collection else tmp_path) / "sc-digger.xml"
    cfg = Config(dict(Config.load(ROOT / "config.yaml").raw))
    cfg.raw["download"] = {**cfg["download"], "inbox_dir": str(inbox), "collection_dir": str(coll)}
    cfg.raw["rekordbox"] = {
        "xml_enabled": enabled,
        "xml_path": str(xml),
        "path_map": {str(inbox): "Z:/Inbox"},
    }
    return cfg, inbox, xml


def test_sync_xml_schreibt_xml_und_meldet_anzahl(tmp_path):
    cfg, inbox, xml = _cfg(tmp_path)
    _audio(inbox / "150-155" / "5A" / "a.mp3", artist="A", title="Eins", bpm=152.0)
    _audio(inbox / "155-160" / "6A" / "b.mp3", artist="B", title="Zwei", bpm=157.0)
    reply = b.sync_xml_reply(cfg, now=JETZT)
    assert reply == "✅ Rekordbox-XML neu geschrieben: 2 Tracks (11.10.2026 17:30)"
    root = ET.parse(xml).getroot()
    assert root.find("COLLECTION").get("Entries") == "2"


def test_sync_xml_leere_inbox_schreibt_leere_xml(tmp_path):
    cfg, _inbox, xml = _cfg(tmp_path)
    reply = b.sync_xml_reply(cfg, now=JETZT)
    assert reply == "✅ Rekordbox-XML neu geschrieben: 0 Tracks (11.10.2026 17:30)"
    assert xml.is_file()


def test_sync_xml_abgeschaltet_schreibt_nichts(tmp_path):
    cfg, _inbox, xml = _cfg(tmp_path, enabled=False)
    assert b.SYNC_XML_DISABLED == "Rekordbox-XML ist abgeschaltet (rekordbox.xml_enabled)."
    assert b.sync_xml_reply(cfg, now=JETZT) == b.SYNC_XML_DISABLED
    assert not xml.exists()


def test_sync_xml_ohne_rekordbox_abschnitt_gilt_als_abgeschaltet(tmp_path):
    cfg, _inbox, _xml = _cfg(tmp_path)
    del cfg.raw["rekordbox"]
    assert b.sync_xml_reply(cfg, now=JETZT) == b.SYNC_XML_DISABLED


def test_sync_xml_ziel_in_der_sammlung_wird_abgelehnt(tmp_path):
    cfg, _inbox, xml = _cfg(tmp_path, xml_in_collection=True)
    assert b.SYNC_XML_FAILED == "Rekordbox-XML konnte nicht geschrieben werden, siehe Container-Log."
    assert b.sync_xml_reply(cfg, now=JETZT) == b.SYNC_XML_FAILED
    assert not xml.exists()


def test_befehl_wird_zugestellt_auch_mit_botnamen():
    cfg = Config(dict(Config.load(ROOT / "config.yaml").raw))
    with patch.object(b, "_send_text") as send, patch.object(b, "sync_xml_reply", return_value="OK") as reply:
        b.handle_message(cfg, None, "42", "/sync_xml@MeinBot")
    reply.assert_called_once_with(cfg)
    send.assert_called_once_with(cfg, "42", "OK")


def test_fehler_im_befehl_wird_gemeldet_statt_geworfen():
    cfg = Config(dict(Config.load(ROOT / "config.yaml").raw))
    with patch.object(b, "_send_text") as send, patch.object(b, "sync_xml_reply", side_effect=RuntimeError("x")):
        b.handle_message(cfg, None, "42", "/sync_xml")
    send.assert_called_once_with(cfg, "42", "XML-Sync fehlgeschlagen, siehe Container-Log.")


def test_hilfetext_nennt_den_befehl():
    assert "/sync_xml" in b.HELP_TEXT
