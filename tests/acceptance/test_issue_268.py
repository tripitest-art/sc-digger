"""Akzeptanztests für Issue 262: Kompakter Rekordbox-Kommentar & Schranz-Farbcodierung."""
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from sc_digger import rekordbox as rb
from sc_digger.schema import get_field, validate_config
from sc_digger.style import (
    StyleAnalysis,
    StyleCluster,
    Subgenre,
    VersionType,
    classify_track,
    format_compact_rekordbox_comment,
    recommend_rekordbox_color,
)


def test_format_compact_rekordbox_comment_alle_felder():
    analysis = classify_track(155.0, title="Hardgroove Stomper", tags=["hardgroove"])
    comment = format_compact_rekordbox_comment(
        analysis,
        bpm=155.0,
        key_camelot="9A",
        drop1_s=45.0,
        drop2_s=135.0,
        lufs=-7.5,
    )
    assert comment == "[155|9A|Hardgroove|D1:0:45|D2:2:15|L-7.5]"


def test_format_compact_rekordbox_comment_teilfelder():
    analysis = classify_track(155.0, title="Hardgroove Stomper", tags=["hardgroove"])
    # Ohne D2 und ohne LUFS
    comment = format_compact_rekordbox_comment(
        analysis,
        bpm=155.0,
        key_camelot="9A",
        drop1_s=45.0,
    )
    assert comment == "[155|9A|Hardgroove|D1:0:45]"

    # Zero-Drop und Ganzzahl-Minuten
    comment_zero = format_compact_rekordbox_comment(analysis, bpm=150.0, drop1_s=0.0)
    assert "D1:0:00" in comment_zero


def test_format_compact_rekordbox_comment_version_type_und_minimal():
    # VersionType TOOL -> Kürzel TOOL statt Subgenre
    tool_analysis = classify_track(150.0, title="Loop Tool (DJ Tool)", tags=["tribal"])
    assert tool_analysis.version_type == VersionType.TOOL
    comment_tool = format_compact_rekordbox_comment(
        tool_analysis,
        bpm=150.0,
        key_camelot="5A",
        drop1_s=65.0,
        lufs=-8.0,
    )
    assert comment_tool == "[150|5A|TOOL|D1:1:05|L-8.0]"

    # Minimal: Ohne Tempo, Tonart, Drops, LUFS
    minimal = classify_track(None, title="Classic Track")
    assert format_compact_rekordbox_comment(minimal) == "[Classic]"


def test_recommend_rekordbox_color_rueckwaertskompatibel_und_matrix():
    analysis = classify_track(150.0, title="Classic Track")

    # by_matrix=False (Standard): wie bisher 'None' für Original
    assert recommend_rekordbox_color(analysis) == "None"
    assert recommend_rekordbox_color(analysis, by_matrix=False) == "None"

    # VersionType-Farben bleiben bei by_matrix=False erhalten
    tool = classify_track(150.0, title="Track (DJ Tool)")
    assert recommend_rekordbox_color(tool, by_matrix=False) == "Blue"

    # by_matrix=True: Farben nach Schranz-Matrix
    assert recommend_rekordbox_color(classify_track(150.0, tags=["classic"]), by_matrix=True) == "Blue"
    assert recommend_rekordbox_color(classify_track(150.0, tags=["tribal"]), by_matrix=True) == "Green"
    assert recommend_rekordbox_color(classify_track(150.0, tags=["hardgroove"]), by_matrix=True) == "Green"
    assert recommend_rekordbox_color(classify_track(160.0, tags=["industrial"]), by_matrix=True) == "Red"
    assert recommend_rekordbox_color(classify_track(170.0, tags=["terror"], lra=2.0), by_matrix=True) == "Red"
    assert recommend_rekordbox_color(classify_track(160.0, tags=["crossbreed"]), by_matrix=True) == "Red"
    assert recommend_rekordbox_color(classify_track(150.0, tags=["melodic"]), by_matrix=True) == "Yellow"
    assert recommend_rekordbox_color(classify_track(155.0, tags=["neo"]), by_matrix=True) == "Orange"
    assert recommend_rekordbox_color(classify_track(150.0, tags=["bounce"]), by_matrix=True) == "Pink"
    assert recommend_rekordbox_color(classify_track(150.0, tags=["acid"]), by_matrix=True) == "Purple"
    assert recommend_rekordbox_color(classify_track(150.0, tags=["psy"]), by_matrix=True) == "Purple"
    assert recommend_rekordbox_color(classify_track(150.0, tags=["reverse_bass"]), by_matrix=True) == "Purple"
    assert recommend_rekordbox_color(classify_track(150.0, tags=["ebm"]), by_matrix=True) == "Purple"


def test_build_xml_compact_comments_und_matrix_colors():
    path_map = {"/test/inbox": "Z:/Inbox"}
    track = rb.InboxTrack(
        path=Path("/test/inbox/hardgroove.mp3"),
        title="Hardgroove Hit",
        artist="DJ Producer",
        genre="hardgroove",
        bpm=155.0,
        key="9A",
        comment="Old",
        duration_s=240.0,
        size=5000,
        added=datetime(2026, 10, 14, 12, 0),
        drop_s=45.0,
        drop2_s=135.0,
        lufs=-7.5,
    )

    # 1. compact_style_comments=True
    xml_comment = rb.build_xml([track], path_map, compact_style_comments=True)
    root_comment = ET.fromstring(xml_comment.split("\n", 1)[1])
    t_comment = root_comment.find("COLLECTION/TRACK")
    assert t_comment is not None
    assert t_comment.get("Comments") == "[155|9A|Hardgroove|D1:0:45|D2:2:15|L-7.5]"

    # 2. matrix_colors=True
    xml_color = rb.build_xml([track], path_map, matrix_colors=True)
    root_color = ET.fromstring(xml_color.split("\n", 1)[1])
    t_color = root_color.find("COLLECTION/TRACK")
    assert t_color is not None
    assert t_color.get("Colour") == "Green"


def test_schema_definition_und_validierung():
    f_matrix = get_field("rekordbox.matrix_colors")
    assert f_matrix is not None
    assert f_matrix.type == "bool"

    f_compact = get_field("rekordbox.compact_style_comments")
    assert f_compact is not None
    assert f_compact.type == "bool"

    cfg = {"rekordbox": {"matrix_colors": True, "compact_style_comments": True}}
    assert validate_config(cfg) == []
