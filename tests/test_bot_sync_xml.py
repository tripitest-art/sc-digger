"""Eigene Tests für den /sync_xml-Bot-Befehl (Issue #187)."""
import xml.etree.ElementTree as ET
from datetime import datetime
from unittest.mock import patch

from sc_digger import bot
from sc_digger.models import Config

JETZT = datetime(2026, 10, 11, 17, 30)


def _cfg(tmp_path, *, enabled=True):
    cfg = Config(dict(Config.load("config.yaml").raw))
    cfg.raw["rekordbox"] = {
        "xml_enabled": enabled,
        "xml_path": str(tmp_path / "sc-digger.xml"),
        "path_map": {},
    }
    return cfg


def _write_xml(path, entries):
    root = ET.Element("DJ_PLAYLISTS")
    ET.SubElement(root, "COLLECTION", {"Entries": str(entries)})
    path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode"))


def test_sync_xml_liest_entries_aus_geschriebener_datei(tmp_path):
    """Die Track-Zahl kommt aus dem Attribut Entries der geschriebenen Datei,
    nicht aus scan_inbox. Der Fake zählt bewusst anders als der Dateibaum."""
    cfg = _cfg(tmp_path)
    xml = tmp_path / "sc-digger.xml"

    def fake_write(_cfg):
        _write_xml(xml, 37)
        return xml

    with patch.object(bot.rekordbox, "write_rekordbox_xml", side_effect=fake_write) as w:
        reply = bot.sync_xml_reply(cfg, now=JETZT)
    w.assert_called_once_with(cfg)
    assert reply == "✅ Rekordbox-XML neu geschrieben: 37 Tracks (11.10.2026 17:30)"


def test_sync_xml_singular_bleibt_tracks(tmp_path):
    cfg = _cfg(tmp_path)
    xml = tmp_path / "sc-digger.xml"

    def fake_write(_cfg):
        _write_xml(xml, 1)
        return xml

    with patch.object(bot.rekordbox, "write_rekordbox_xml", side_effect=fake_write):
        reply = bot.sync_xml_reply(cfg, now=JETZT)
    assert reply == "✅ Rekordbox-XML neu geschrieben: 1 Tracks (11.10.2026 17:30)"


def test_sync_xml_abgeschaltet_ruft_writer_nicht(tmp_path):
    cfg = _cfg(tmp_path, enabled=False)
    with patch.object(bot.rekordbox, "write_rekordbox_xml") as w:
        reply = bot.sync_xml_reply(cfg, now=JETZT)
    w.assert_not_called()
    assert reply == bot.SYNC_XML_DISABLED


def test_sync_xml_writer_fehler_wird_gemeldet(tmp_path):
    cfg = _cfg(tmp_path)
    with patch.object(bot.rekordbox, "write_rekordbox_xml", return_value=None):
        assert bot.sync_xml_reply(cfg, now=JETZT) == bot.SYNC_XML_FAILED


def test_sync_xml_kaputte_datei_wirft_nicht(tmp_path):
    """Auch ein Fehler beim Lesen der geschriebenen Datei endet in SYNC_XML_FAILED."""
    cfg = _cfg(tmp_path)
    xml = tmp_path / "sc-digger.xml"
    xml.write_text("kein xml")

    with patch.object(bot.rekordbox, "write_rekordbox_xml", return_value=xml):
        assert bot.sync_xml_reply(cfg, now=JETZT) == bot.SYNC_XML_FAILED


def test_sync_xml_in_help_text():
    assert "/sync_xml" in bot.HELP_TEXT


def test_sync_xml_befehl_wird_zugestellt():
    cfg = Config(dict(Config.load("config.yaml").raw))
    with patch.object(bot, "_send_text") as send, patch.object(bot, "sync_xml_reply", return_value="OK") as reply:
        bot.handle_message(cfg, None, "42", "/sync_xml")
    reply.assert_called_once_with(cfg)
    send.assert_called_once_with(cfg, "42", "OK")
