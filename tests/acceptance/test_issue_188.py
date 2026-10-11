"""Akzeptanztests: Rekordbox-XML mit Playlists nach BPM-Bereich und Camelot-Key."""
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from sc_digger import rekordbox as rb
from sc_digger.models import Config
from sc_digger.organize import write_tags

ROOT = Path(__file__).resolve().parents[2]
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
    top = root.find("PLAYLISTS/NODE/NODE")  # Ordner "sc-digger"
    assert top.get("Name") == "sc-digger"
    for node in top:
        if node.get("Type") == "0" and node.get("Name") == name:
            return node
    return None


def _lists(folder: ET.Element, ids: dict[str, str]) -> list[tuple[str, list[str]]]:
    return [(n.get("Name"), [ids[t.get("Key")] for t in n]) for n in folder]


def test_ohne_neue_optionen_bleibt_die_xml_wie_bisher():
    tracks = [_t("a", 152.0, "5A"), _t("b", 157.0, "6A")]
    root = _parse(rb.build_xml(tracks, PATH_MAP))
    top = root.find("PLAYLISTS/NODE/NODE")
    assert [n.get("Name").startswith("KW ") for n in top] == [True]
    assert top.get("Count") == "1"
    assert _folder(root, "BPM") is None and _folder(root, "Key") is None


def test_bpm_playlists_numerisch_sortiert_mit_unbekannt_zuletzt():
    tracks = [_t("a", 152.3, "5A"), _t("b", 153.0, "5A"), _t("c", 157.9, "6A"),
              _t("d", None, "6A"), _t("e", 99.5, "1A")]
    root = _parse(rb.build_xml(tracks, PATH_MAP, bpm_bucket_size=5))
    folder = _folder(root, "BPM")
    assert folder.get("Count") == "4"
    assert _lists(folder, _ids(root)) == [
        ("95-100", ["e"]), ("150-155", ["a", "b"]), ("155-160", ["c"]), ("unbekannt", ["d"]),
    ]
    for pl in folder:
        assert (pl.get("Type"), pl.get("KeyType"), pl.get("Entries")) == ("1", "0", str(len(pl)))


def test_bpm_bereichsbreite_folgt_dem_parameter():
    root = _parse(rb.build_xml([_t("a", 152.0, "5A"), _t("b", 158.0, "5A")], PATH_MAP, bpm_bucket_size=10))
    assert _lists(_folder(root, "BPM"), _ids(root)) == [("150-160", ["a", "b"])]


def test_key_playlists_nach_camelot_sortiert_rest_ist_unbekannt():
    tracks = [_t("a", 150.0, "5A"), _t("b", 150.0, "5A"), _t("c", 150.0, "12B"),
              _t("d", 150.0, "1A"), _t("e", 150.0, ""), _t("f", 150.0, "Am"), _t("g", 150.0, "5B")]
    root = _parse(rb.build_xml(tracks, PATH_MAP, by_key=True))
    folder = _folder(root, "Key")
    assert folder.get("Count") == "5"
    assert _lists(folder, _ids(root)) == [
        ("1A", ["d"]), ("5A", ["a", "b"]), ("5B", ["g"]), ("12B", ["c"]), ("unbekannt", ["e", "f"]),
    ]
    assert _folder(root, "BPM") is None


def test_beide_ordner_stehen_nach_den_wochen_und_jeder_track_einmal_je_ordner():
    tracks = [_t("a", 152.0, "5A", datetime(2026, 10, 5)), _t("b", 157.0, "6A", datetime(2026, 9, 28))]
    root = _parse(rb.build_xml(tracks, PATH_MAP, bpm_bucket_size=5, by_key=True))
    top = root.find("PLAYLISTS/NODE/NODE")
    assert [n.get("Name").startswith("KW ") for n in top] == [True, True, False, False]
    assert [n.get("Name") for n in top][2:] == ["BPM", "Key"]
    assert top.get("Count") == "4"
    for name in ("BPM", "Key"):
        folder = _folder(root, name)
        assert folder.get("Type") == "0"
        assert sum(len(pl) for pl in folder) == 2
    for node in root.iter("NODE"):
        if node.get("Type") == "0" and node.get("Name") != "ROOT":
            assert node.get("Count") == str(len(node))


def test_nicht_gemappte_tracks_fehlen_auch_in_den_neuen_playlists():
    tracks = [_t("a", 152.0, "5A"), _t("b", 152.0, "5A", root="/anderswo")]
    root = _parse(rb.build_xml(tracks, PATH_MAP, bpm_bucket_size=5, by_key=True))
    assert _lists(_folder(root, "BPM"), _ids(root)) == [("150-155", ["a"])]
    assert _lists(_folder(root, "Key"), _ids(root)) == [("5A", ["a"])]


def test_leere_inbox_erzeugt_keine_leeren_ordner():
    root = _parse(rb.build_xml([], PATH_MAP, bpm_bucket_size=5, by_key=True))
    assert _folder(root, "BPM") is None and _folder(root, "Key") is None


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


def test_schreiben_ohne_optionen_erzeugt_keine_neuen_ordner(tmp_path):
    cfg = _cfg(tmp_path)
    xml = rb.write_rekordbox_xml(cfg)
    root = ET.parse(xml).getroot()
    assert _folder(root, "BPM") is None and _folder(root, "Key") is None


def test_schreiben_mit_optionen_nutzt_bucket_aus_organize(tmp_path):
    cfg = _cfg(tmp_path, playlists_by_bpm=True, playlists_by_key=True)
    cfg.raw["organize"] = {**cfg["organize"], "bpm_bucket_size": 10}
    root = ET.parse(rb.write_rekordbox_xml(cfg)).getroot()
    assert [n.get("Name") for n in _folder(root, "BPM")] == ["150-160"]
    assert [n.get("Name") for n in _folder(root, "Key")] == ["unbekannt"]


def test_ungueltige_bucketgroesse_faellt_auf_fuenf_zurueck(tmp_path):
    cfg = _cfg(tmp_path, playlists_by_bpm=True)
    cfg.raw["organize"] = {**cfg["organize"], "bpm_bucket_size": 0}
    root = ET.parse(rb.write_rekordbox_xml(cfg)).getroot()
    assert [n.get("Name") for n in _folder(root, "BPM")] == ["150-155"]
    assert _folder(root, "Key") is None


def test_config_yaml_schaltet_beide_playlists_ein():
    section = Config.load(ROOT / "config.yaml")["rekordbox"]
    assert section["playlists_by_bpm"] is True
    assert section["playlists_by_key"] is True
