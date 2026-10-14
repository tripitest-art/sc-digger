"""Akzeptanztests: Beatgrid-Anker (<TEMPO>) im Rekordbox-XML-Export."""

from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET
import pytest

from sc_digger.models import Config
from sc_digger import rekordbox as rb

PATH_MAP = {"/test/inbox": "Z:/Inbox"}


def _t(
    name: str,
    first_kick_s: float | None = 0.045,
    bpm: float | None = 155.0,
) -> rb.InboxTrack:
  return rb.InboxTrack(
      path=Path("/test/inbox") / "155" / "5A" / f"{name}.wav",
      title=name,
      artist="Artist",
      genre="Schranz",
      bpm=bpm,
      key="5A",
      comment="",
      duration_s=300.0,
      size=1000,
      added=datetime(2026, 10, 14, 12, 0),
      first_kick_s=first_kick_s,
  )


def _parse(xml_str: str) -> ET.Element:
  return ET.fromstring(xml_str.split("\n", 1)[1])


def test_build_xml_grid_anchor_standard_erzeugt_tempo_knoten():
  """Standard (grid_anchor=True) erzeugt <TEMPO> mit exaktem Beginning und Bpm an first_kick_s."""
  track = _t("t1", first_kick_s=0.045, bpm=155.0)
  xml = rb.build_xml([track], PATH_MAP)
  root = _parse(xml)
  track_node = root.find("COLLECTION/TRACK")
  assert track_node is not None
  tempo = track_node.find("TEMPO")
  assert tempo is not None
  assert tempo.attrib == {
      "Beginning": "0.045",
      "Bpm": "155.00",
      "Metro": "4/4",
      "Battito": "1",
  }


def test_build_xml_grid_anchor_position_vor_position_marks():
  """TEMPO-Knoten muss in Rekordbox-XML vor den POSITION_MARK-Elementen stehen."""
  track = _t("t1", first_kick_s=0.045, bpm=155.0)
  xml = rb.build_xml([track], PATH_MAP, grid_anchor=True, hot_cues=True)
  root = _parse(xml)
  track_node = root.find("COLLECTION/TRACK")
  assert track_node is not None
  tags = [child.tag for child in track_node]
  assert "TEMPO" in tags
  assert "POSITION_MARK" in tags
  assert tags.index("TEMPO") < tags.index("POSITION_MARK")


def test_build_xml_grid_anchor_false_deaktiviert_tempo_knoten():
  """grid_anchor=False unterdrückt den TEMPO-Knoten."""
  track = _t("t1", first_kick_s=0.045, bpm=155.0)
  xml = rb.build_xml([track], PATH_MAP, grid_anchor=False)
  root = _parse(xml)
  assert root.find("COLLECTION/TRACK/TEMPO") is None


def test_build_xml_grid_anchor_ohne_kick_oder_bpm_erzeugt_kein_tempo():
  """Fehlender Kick oder fehlende BPM erzeugt keinen TEMPO-Knoten."""
  track_no_kick = _t("no_kick", first_kick_s=None, bpm=155.0)
  track_no_bpm = _t("no_bpm", first_kick_s=0.045, bpm=None)
  xml = rb.build_xml([track_no_kick, track_no_bpm], PATH_MAP, grid_anchor=True)
  root = _parse(xml)
  tracks = root.findall("COLLECTION/TRACK")
  assert len(tracks) == 2
  assert tracks[0].find("TEMPO") is None
  assert tracks[1].find("TEMPO") is None


def test_write_rekordbox_xml_grid_anchor_integration(tmp_path, monkeypatch):
  """write_rekordbox_xml reicht rekordbox.grid_anchor an build_xml weiter."""
  track = _t("t1", first_kick_s=0.045, bpm=155.0)
  monkeypatch.setattr(rb, "scan_inbox", lambda _: [track])
  xml_out = tmp_path / "rekordbox.xml"
  cfg = Config({
      "download": {
          "inbox_dir": str(tmp_path / "inbox"),
          "collection_dir": str(tmp_path / "coll"),
      },
      "rekordbox": {
          "xml_enabled": True,
          "xml_path": str(xml_out),
          "path_map": {"/test/inbox": "Z:/Inbox"},
          "grid_anchor": True,
      },
  })
  res_path = rb.write_rekordbox_xml(cfg)
  assert res_path == xml_out
  root = _parse(xml_out.read_text(encoding="utf-8"))
  assert root.find("COLLECTION/TRACK/TEMPO") is not None

  cfg.raw["rekordbox"]["grid_anchor"] = False
  rb.write_rekordbox_xml(cfg)
  root_off = _parse(xml_out.read_text(encoding="utf-8"))
  assert root_off.find("COLLECTION/TRACK/TEMPO") is None


def test_schema_und_config_grid_anchor():
  """Config-Schema und config.yaml enthalten rekordbox.grid_anchor."""
  from sc_digger.schema import FIELDS, validate_config

  field_paths = {f.path for f in FIELDS}
  assert "rekordbox.grid_anchor" in field_paths

  root_dir = Path(__file__).resolve().parents[2]
  if not (root_dir / "config.yaml").exists():
    root_dir = Path.cwd()
  cfg = Config.load(root_dir / "config.yaml")
  assert cfg.raw.get("rekordbox", {}).get("grid_anchor") is True
  assert validate_config(cfg.raw) == []
