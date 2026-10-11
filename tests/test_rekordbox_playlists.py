"""Eigene Tests für Rekordbox-Playlists nach BPM-Bereich und Camelot-Key (sc_digger.rekordbox)."""
from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from sc_digger import organize
from sc_digger import rekordbox as rb
from sc_digger.models import Config
from sc_digger.organize import write_tags

ROOT = Path(__file__).resolve().parents[1]
PATH_MAP = {"/music/inbox": "Z:/Inbox"}


def _t(name, bpm, key, added=datetime(2026, 10, 5, 12, 0), root="/music/inbox"):
    return rb.InboxTrack(
        path=Path(root) / "x" / f"{name}.mp3", title=name, artist="A", genre="Hardtechno",
        bpm=bpm, key=key, comment="", duration_s=300.0, size=1000, added=added,
    )


def _parse(xml: str) -> ET.Element:
    return ET.fromstring(xml.split("\n", 1)[1])


def _ids(root: ET.Element) -> dict[str, str]:
    return {t.get("TrackID"): t.get("Name") for t in root.find("COLLECTION")}


def _folder(root: ET.Element, name: str):
    top = root.find("PLAYLISTS/NODE/NODE")
    for node in top:
        if node.get("Type") == "0" and node.get("Name") == name:
            return node
    return None


def _lists(folder: ET.Element, ids: dict[str, str]) -> list[tuple[str, list[str]]]:
    return [(n.get("Name"), [ids[t.get("Key")] for t in n]) for n in folder]


def test_bpm_bucket_label_kommt_aus_organize():
    """Die BPM-Playlist-Namen werden aus organize._bpm_bucket abgeleitet (nicht kopiert)."""
    tracks = [_t("a", 152.0, "5A"), _t("b", 157.0, "5A")]
    root = _parse(rb.build_xml(tracks, PATH_MAP, bpm_bucket_size=5))
    names = [n.get("Name") for n in _folder(root, "BPM")]
    assert names == [organize._bpm_bucket(152.0, 5), organize._bpm_bucket(157.0, 5)]
    assert names == ["150-155", "155-160"]


def test_bpm_none_bekommt_unbekannt_nicht_unknown_bpm():
    """organize liefert "unknown-bpm"; die XML-Playlist heisst laut Issue aber "unbekannt"."""
    root = _parse(rb.build_xml([_t("a", None, "5A")], PATH_MAP, bpm_bucket_size=5))
    assert [n.get("Name") for n in _folder(root, "BPM")] == ["unbekannt"]


def test_key_ordner_wird_weggelassen_wenn_by_key_false():
    root = _parse(rb.build_xml([_t("a", 150.0, "5A")], PATH_MAP, by_key=False))
    assert _folder(root, "Key") is None
    assert _folder(root, "BPM") is None


def test_key_kleinbuchstaben_und_leerraum_werden_normalisiert():
    tracks = [_t("a", 150.0, " 5a "), _t("b", 150.0, "5A")]
    root = _parse(rb.build_xml(tracks, PATH_MAP, by_key=True))
    assert _lists(_folder(root, "Key"), _ids(root)) == [("5A", ["a", "b"])]


def test_ungueltige_keys_landen_zusammen_in_unbekannt():
    tracks = [_t("a", 150.0, "Hm"), _t("b", 150.0, "13A"), _t("c", 150.0, "0B"), _t("d", 150.0, "1C")]
    root = _parse(rb.build_xml(tracks, PATH_MAP, by_key=True))
    assert _lists(_folder(root, "Key"), _ids(root)) == [("unbekannt", ["a", "b", "c", "d"])]


def test_sc_digger_count_zaehlt_wochen_und_ordner():
    tracks = [_t("a", 152.0, "5A", datetime(2026, 10, 5)),
              _t("b", 157.0, "6A", datetime(2026, 9, 28))]
    root = _parse(rb.build_xml(tracks, PATH_MAP, bpm_bucket_size=5, by_key=True))
    top = root.find("PLAYLISTS/NODE/NODE")
    assert top.get("Count") == "4"  # 2 Wochen + BPM + Key


def test_sc_digger_count_ohne_neue_ordner_bleibt_wochenzahl():
    root = _parse(rb.build_xml([_t("a", 152.0, "5A")], PATH_MAP))
    top = root.find("PLAYLISTS/NODE/NODE")
    assert top.get("Count") == "1"


def test_nur_bpm_ordner_zaehlt_nicht_den_key_ordner():
    root = _parse(rb.build_xml([_t("a", 152.0, "5A")], PATH_MAP, bpm_bucket_size=5, by_key=False))
    top = root.find("PLAYLISTS/NODE/NODE")
    assert top.get("Count") == "2"
    assert _folder(root, "Key") is None


def test_by_key_mit_leerer_inbox_erzeugt_keinen_key_ordner():
    root = _parse(rb.build_xml([], PATH_MAP, by_key=True))
    assert _folder(root, "Key") is None


def _audio(path: Path, **tags) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "anoisesrc=d=1:c=pink:r=44100:a=0.2", "-ac", "2", "-b:a", "320k", str(path)],
        check=True,
    )
    write_tags(path, **tags)
    return path


def _cfg(tmp_path, **rekordbox_opts):
    inbox = tmp_path / "inbox"
    coll = tmp_path / "coll"
    inbox.mkdir()
    coll.mkdir()
    _audio(inbox / "150-155" / "5A" / "a.mp3", artist="A", title="Eins", bpm=152.0)
    cfg = Config(dict(Config.load(ROOT / "config.yaml").raw))
    cfg.raw["download"] = {**cfg["download"], "inbox_dir": str(inbox), "collection_dir": str(coll)}
    cfg.raw["rekordbox"] = {
        "xml_enabled": True, "xml_path": str(tmp_path / "sc-digger.xml"),
        "path_map": {str(inbox): "Z:/Inbox"}, **rekordbox_opts,
    }
    return cfg


def test_schreiben_mit_fehlender_bucketoption_nutzt_fuenf(tmp_path):
    cfg = _cfg(tmp_path, playlists_by_bpm=True)
    cfg.raw["organize"] = {k: v for k, v in cfg["organize"].items() if k != "bpm_bucket_size"}
    root = ET.parse(rb.write_rekordbox_xml(cfg)).getroot()
    assert [n.get("Name") for n in _folder(root, "BPM")] == ["150-155"]


def test_schreiben_mit_string_bucketgroesse_faellt_auf_fuenf_zurueck(tmp_path):
    cfg = _cfg(tmp_path, playlists_by_bpm=True)
    cfg.raw["organize"] = {**cfg["organize"], "bpm_bucket_size": "10"}
    root = ET.parse(rb.write_rekordbox_xml(cfg)).getroot()
    assert [n.get("Name") for n in _folder(root, "BPM")] == ["150-155"]


def test_schreiben_by_key_false_erzeugt_keinen_key_ordner(tmp_path):
    cfg = _cfg(tmp_path, playlists_by_key=False)
    root = ET.parse(rb.write_rekordbox_xml(cfg)).getroot()
    assert _folder(root, "Key") is None


def test_schreiben_by_key_true_mit_track_ohne_key_nutzt_unbekannt(tmp_path):
    cfg = _cfg(tmp_path, playlists_by_key=True)
    root = ET.parse(rb.write_rekordbox_xml(cfg)).getroot()
    assert [n.get("Name") for n in _folder(root, "Key")] == ["unbekannt"]


def test_schreiben_by_key_true_mit_camelot_key(tmp_path):
    cfg = _cfg(tmp_path, playlists_by_key=True)
    _audio(
        Path(cfg["download"]["inbox_dir"]) / "150-155" / "5A" / "b.mp3",
        artist="B", title="Zwei", bpm=153.0, key_name="5A",
    )
    root = ET.parse(rb.write_rekordbox_xml(cfg)).getroot()
    assert sorted(n.get("Name") for n in _folder(root, "Key")) == ["5A", "unbekannt"]
