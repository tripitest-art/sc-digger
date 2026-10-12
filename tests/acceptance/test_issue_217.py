"""Akzeptanztests: Die Schranz-Matrix, Teil 2: Rekordbox-XML Playlists für Stile, Subgenres und Versionen (Farben & Kommentare)."""
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from sc_digger import rekordbox as rb
from sc_digger.models import Config
from sc_digger.organize import write_tags
from sc_digger.schema import get_field, validate_config

ROOT = Path(__file__).resolve().parents[2]
PATH_MAP = {"/music/inbox": "Z:/Inbox"}


def _t(name, bpm, key, genre="", comment="Alt", added=datetime(2026, 10, 5, 12, 0), root="/music/inbox"):
    return rb.InboxTrack(
        path=Path(root) / "x" / f"{name}.mp3",
        title=name,
        artist="DJ Test",
        genre=genre,
        bpm=bpm,
        key=key,
        comment=comment,
        duration_s=300.0,
        size=1000,
        added=added,
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


def test_build_xml_ohne_stil_optionen_bleibt_rueckwaertskompatibel():
    tracks = [
        _t("Krenon - Impact (DJ Tool)", 150.0, "5A", genre="tribal", comment="Mein Tool"),
        _t("Oldie - Hit (Bootleg)", 155.0, "8A", genre="neo", comment="Mein Bootleg"),
    ]
    root = _parse(rb.build_xml(tracks, PATH_MAP))

    # Keine Colour-Attribute gesetzt
    coll = root.find("COLLECTION")
    for track_node in coll.findall("TRACK"):
        assert "Colour" not in track_node.attrib

    # Kommentare unverändert
    track_comments = [t.get("Comments") for t in coll.findall("TRACK")]
    assert track_comments == ["Mein Tool", "Mein Bootleg"]

    # Keine Stil-Ordner erzeugt
    assert _folder(root, "Stile") is None
    assert _folder(root, "Subgenres") is None
    assert _folder(root, "Versionen") is None

    # Count entspricht nur den Wochen
    top = root.find("PLAYLISTS/NODE/NODE")
    assert top.get("Count") == "1"


def test_build_xml_style_colors_setzt_farbe_nur_bei_nicht_none():
    tracks = [
        _t("Krenon - Impact (DJ Tool)", 150.0, "5A"),          # TOOL -> Blue
        _t("Producer - Track (DJ Edit)", 152.0, "2A"),         # EDIT -> Green
        _t("Madonna - Hung Up (Bootleg)", 155.0, "8A"),        # BOOTLEG -> Red
        _t("Acapella vs Beat (Mashup)", 154.0, "1A"),          # MASHUP -> Red
        _t("Classic - Tune (Jersey Flip)", 150.0, "3A"),       # FLIP -> Red
        _t("Artist - Anthem (VIP)", 155.0, "6A"),              # VIP -> Yellow
        _t("Oldie - Hit (2024 Rework)", 152.0, "4A"),          # REWORK -> Yellow
        _t("Regular Track (Original Mix)", 150.0, "7A"),       # ORIGINAL -> None
        _t("Artist - Song (Remix)", 150.0, "9A"),              # REMIX -> None
    ]

    # Mit style_colors=True
    root_colors = _parse(rb.build_xml(tracks, PATH_MAP, style_colors=True))
    coll = root_colors.find("COLLECTION")
    tracks_by_name = {t.get("Name"): t for t in coll.findall("TRACK")}

    assert tracks_by_name["Krenon - Impact (DJ Tool)"].get("Colour") == "Blue"
    assert tracks_by_name["Producer - Track (DJ Edit)"].get("Colour") == "Green"
    assert tracks_by_name["Madonna - Hung Up (Bootleg)"].get("Colour") == "Red"
    assert tracks_by_name["Acapella vs Beat (Mashup)"].get("Colour") == "Red"
    assert tracks_by_name["Classic - Tune (Jersey Flip)"].get("Colour") == "Red"
    assert tracks_by_name["Artist - Anthem (VIP)"].get("Colour") == "Yellow"
    assert tracks_by_name["Oldie - Hit (2024 Rework)"].get("Colour") == "Yellow"
    assert "Colour" not in tracks_by_name["Regular Track (Original Mix)"].attrib
    assert "Colour" not in tracks_by_name["Artist - Song (Remix)"].attrib

    # Mit style_colors=False
    root_no_colors = _parse(rb.build_xml(tracks, PATH_MAP, style_colors=False))
    for t_node in root_no_colors.find("COLLECTION").findall("TRACK"):
        assert "Colour" not in t_node.attrib


def test_build_xml_style_comments_formatiert_kommentare():
    tracks = [
        _t("Donk Anthem", 154.0, "5A", genre="bounce", comment="Alt"),
        _t("Krenon - Impact (DJ Tool)", 150.0, "11A", genre="tribal", comment="Alt"),
        _t("Hardgroove Track (DJ Edit)", 146.0, "2A", genre="hardgroove", comment="Alt"),
    ]

    root = _parse(rb.build_xml(tracks, PATH_MAP, style_comments=True))
    coll = root.find("COLLECTION")
    comments = {t.get("Name"): t.get("Comments") for t in coll.findall("TRACK")}

    assert comments["Donk Anthem"] == "[Bounce] 154 BPM · 5A | Vertikal · ⚠️ Subbass-Phasengefahr!"
    assert comments["Krenon - Impact (DJ Tool)"] == "[TOOL] 150 BPM · 11A | Horizontal · 3-Deck Loop Tool"
    assert comments["Hardgroove Track (DJ Edit)"] == "[EDIT] 146 BPM · 2A | Horizontal · Ext. Intro/Outro"

    # Gegenprobe mit style_comments=False
    root_plain = _parse(rb.build_xml(tracks, PATH_MAP, style_comments=False))
    for t_node in root_plain.find("COLLECTION").findall("TRACK"):
        assert t_node.get("Comments") == "Alt"


def test_build_xml_by_style_erzeugt_ordner_stile_in_fester_reihenfolge():
    tracks = [
        _t("T1", 142.0, "11A", genre="tribal"),       # FUNDAMENT
        _t("T2", 155.0, "1A", genre="industrial"),     # NOISE
        _t("T3", 155.0, "8A", genre="neo"),            # EMOTION
        _t("T4", 154.0, "5A", genre="bounce"),         # CROSSOVER
    ]
    root = _parse(rb.build_xml(tracks, PATH_MAP, by_style=True))
    folder = _folder(root, "Stile")
    assert folder is not None
    assert folder.get("Count") == "4"

    lists = _lists(folder, _ids(root))
    assert lists == [
        ("Fundament & Tools", ["T1"]),
        ("Noise & Extremhärte", ["T2"]),
        ("Emotion & Dramaturgie", ["T3"]),
        ("Rhythmus-Crossover", ["T4"]),
    ]


def test_build_xml_by_style_ueberspringt_leere_playlists():
    tracks = [
        _t("T1", 142.0, "11A", genre="tribal"),   # FUNDAMENT
        _t("T2", 154.0, "5A", genre="bounce"),     # CROSSOVER
    ]
    root = _parse(rb.build_xml(tracks, PATH_MAP, by_style=True))
    folder = _folder(root, "Stile")
    assert folder is not None
    assert folder.get("Count") == "2"

    lists = _lists(folder, _ids(root))
    assert lists == [
        ("Fundament & Tools", ["T1"]),
        ("Rhythmus-Crossover", ["T2"]),
    ]


def test_build_xml_by_subgenre_in_fester_matrix_reihenfolge():
    tracks = [
        _t("S_Crossbreed", 160.0, "5A", genre="crossbreed"),
        _t("S_Tribal", 142.0, "11A", genre="tribal"),
        _t("S_Terror", 168.0, "1A", genre="terror"),
        _t("S_Acid", 154.0, "4A", genre="acid"),
        _t("S_Bounce", 154.0, "5A", genre="bounce"),
    ]
    root = _parse(rb.build_xml(tracks, PATH_MAP, by_subgenre=True))
    folder = _folder(root, "Subgenres")
    assert folder is not None
    assert folder.get("Count") == "5"

    names = [pl_name for pl_name, _ in _lists(folder, _ids(root))]
    # Matrix-Reihenfolge: Tribal vor Acid vor Terror vor Bounce vor Crossbreed
    assert names == ["Tribal", "Acid", "Terror", "Bounce", "Crossbreed"]

    lists = _lists(folder, _ids(root))
    assert lists == [
        ("Tribal", ["S_Tribal"]),
        ("Acid", ["S_Acid"]),
        ("Terror", ["S_Terror"]),
        ("Bounce", ["S_Bounce"]),
        ("Crossbreed", ["S_Crossbreed"]),
    ]


def test_build_xml_by_version_in_fester_reihenfolge():
    tracks = [
        _t("Song (Original Mix)", 150.0, "5A"),
        _t("Beat (Jersey Flip)", 152.0, "5A"),
        _t("Banger (Bootleg)", 155.0, "5A"),
        _t("Loop (DJ Tool)", 150.0, "5A"),
        _t("Track (VIP)", 154.0, "5A"),
    ]
    root = _parse(rb.build_xml(tracks, PATH_MAP, by_version=True))
    folder = _folder(root, "Versionen")
    assert folder is not None
    assert folder.get("Count") == "5"

    names = [pl_name for pl_name, _ in _lists(folder, _ids(root))]
    # Reihenfolge: Tools, Bootlegs, VIPs, Flips, Originale
    assert names == ["Tools", "Bootlegs", "VIPs", "Flips", "Originale"]

    lists = _lists(folder, _ids(root))
    assert lists == [
        ("Tools", ["Loop (DJ Tool)"]),
        ("Bootlegs", ["Banger (Bootleg)"]),
        ("VIPs", ["Track (VIP)"]),
        ("Flips", ["Beat (Jersey Flip)"]),
        ("Originale", ["Song (Original Mix)"]),
    ]


def test_build_xml_alle_ordner_zusammen_und_sc_digger_count():
    tracks = [
        _t("T1 (DJ Tool)", 152.0, "5A", genre="tribal", added=datetime(2026, 10, 5)),
        _t("T2 (Bootleg)", 157.0, "6A", genre="acid", added=datetime(2026, 9, 28)),
    ]
    root = _parse(rb.build_xml(
        tracks,
        PATH_MAP,
        bpm_bucket_size=5,
        by_key=True,
        by_style=True,
        by_subgenre=True,
        by_version=True,
    ))

    top = root.find("PLAYLISTS/NODE/NODE")
    # 2 Wochen + 5 Ordner (BPM, Key, Stile, Subgenres, Versionen) = 7
    assert top.get("Count") == "7"

    folder_names = [n.get("Name") for n in top if n.get("Type") == "0"]
    assert folder_names == ["BPM", "Key", "Stile", "Subgenres", "Versionen"]

    for name in folder_names:
        f = _folder(root, name)
        assert f.get("Type") == "0"
        assert int(f.get("Count")) == len(list(f))


def test_build_xml_leere_inbox_erzeugt_keine_stil_ordner():
    root = _parse(rb.build_xml(
        [],
        PATH_MAP,
        by_style=True,
        by_subgenre=True,
        by_version=True,
        style_colors=True,
        style_comments=True,
    ))
    assert _folder(root, "Stile") is None
    assert _folder(root, "Subgenres") is None
    assert _folder(root, "Versionen") is None
    top = root.find("PLAYLISTS/NODE/NODE")
    assert top.get("Count") == "0"


def _audio(path: Path, **tags) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
            "anoisesrc=d=1:c=pink:r=44100:a=0.2", "-ac", "2", "-b:a", "320k", str(path),
        ],
        check=True,
    )
    write_tags(path, **tags)
    return path


def test_write_rekordbox_xml_mit_allen_stil_optionen(tmp_path):
    inbox = tmp_path / "inbox"
    coll = tmp_path / "coll"
    inbox.mkdir()
    coll.mkdir()

    _audio(
        inbox / "150-155" / "5A" / "track1.mp3",
        artist="DJ Toolmaker",
        title="Impact (DJ Tool)",
        bpm=152.0,
        key_name="5A",
        genre="tribal",
    )
    _audio(
        inbox / "155-160" / "8A" / "track2.mp3",
        artist="Bootlegger",
        title="Classic Hit (Hardtechno Bootleg)",
        bpm=158.0,
        key_name="8A",
        genre="neo",
    )

    cfg = Config(dict(Config.load(ROOT / "config.yaml").raw))
    cfg.raw["download"] = {**cfg["download"], "inbox_dir": str(inbox), "collection_dir": str(coll)}
    cfg.raw["rekordbox"] = {
        "xml_enabled": True,
        "xml_path": str(tmp_path / "sc-digger.xml"),
        "path_map": {str(inbox): "Z:/Inbox"},
        "playlists_by_style": True,
        "playlists_by_subgenre": True,
        "playlists_by_version": True,
        "style_colors": True,
        "style_comments": True,
    }

    xml_path = rb.write_rekordbox_xml(cfg)
    assert xml_path is not None and xml_path.exists()

    root = ET.parse(xml_path).getroot()
    coll_node = root.find("COLLECTION")
    tracks = {t.get("Name"): t for t in coll_node.findall("TRACK")}

    tool_track = tracks["Impact (DJ Tool)"]
    assert tool_track.get("Colour") == "Blue"
    assert "[TOOL] 152 BPM · 5A | Horizontal · 3-Deck Loop Tool" in tool_track.get("Comments")

    bootleg_track = tracks["Classic Hit (Hardtechno Bootleg)"]
    assert bootleg_track.get("Colour") == "Red"
    assert "[BOOTLEG] 158 BPM · 8A | Vertikal · ⚠️ Stopp & Drop" in bootleg_track.get("Comments")

    assert _folder(root, "Stile") is not None
    assert _folder(root, "Subgenres") is not None
    assert _folder(root, "Versionen") is not None


def test_schema_und_config_yaml_stil_optionen():
    fields = [
        "rekordbox.playlists_by_style",
        "rekordbox.playlists_by_subgenre",
        "rekordbox.playlists_by_version",
        "rekordbox.style_colors",
        "rekordbox.style_comments",
    ]
    for p in fields:
        f = get_field(p)
        assert f is not None, f"Feld {p} fehlt in FIELDS"
        assert f.kind == "bool"
        assert f.editable is True
        assert len(f.label) <= 40
        assert f.help.strip()

    # config.yaml enthält alle 5 Felder und ist gültig
    cfg = Config.load(ROOT / "config.yaml")
    rb_cfg = cfg["rekordbox"]
    for p in fields:
        key = p.split(".", 1)[1]
        assert key in rb_cfg
        assert rb_cfg[key] is False

    problems = validate_config(cfg.raw)
    assert [p for p in problems if p.level == "error"] == []
