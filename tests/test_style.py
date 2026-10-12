"""Eigene Tests des Workers für sc_digger/style.py: Randfälle und Keyword-Variationen."""
from __future__ import annotations

import pytest

from sc_digger.style import (
    Aesthetic,
    FlowAxis,
    RhythmRaster,
    StyleAnalysis,
    StyleCluster,
    Subgenre,
    VersionType,
    calculate_energy,
    classify_track,
    detect_version_type,
    format_rekordbox_comment,
    recommend_rekordbox_color,
)


def _analysis(subgenre: Subgenre, version_type: VersionType) -> StyleAnalysis:
    return StyleAnalysis(
        cluster=StyleCluster.FUNDAMENT,
        subgenre=subgenre,
        flow=FlowAxis.HORIZONTAL,
        raster=RhythmRaster.STRAIGHT_4_4,
        aesthetic=Aesthetic.PERCUSSIVE_ATONAL,
        energy=3,
        dj_note="Notiz",
        confidence=0.9,
        version_type=version_type,
    )


@pytest.mark.parametrize(
    "tag, expected",
    [
        ("Tribal", Subgenre.TRIBAL),
        ("HARDGROOVE", Subgenre.HARDGROOVE),
        ("Hard Groove", Subgenre.HARDGROOVE),
        ("Reverse_Bass", Subgenre.REVERSE_BASS),
        ("reverse bass", Subgenre.REVERSE_BASS),
        ("reversebass", Subgenre.REVERSE_BASS),
        ("breakcore", Subgenre.CROSSBREED),
        ("amen", Subgenre.CROSSBREED),
        ("donk", Subgenre.BOUNCE),
        ("EBM", Subgenre.EBM),
    ],
)
def test_keyword_variationen(tag, expected):
    res = classify_track(150.0, tags=[tag])
    assert res.subgenre == expected


def test_titel_stichwort_wenn_keine_tags():
    res = classify_track(150.0, title="Acid 303 Massaker")
    assert res.subgenre == Subgenre.ACID
    assert res.cluster == StyleCluster.NOISE


def test_tags_schlagen_audio_subgenre():
    # Tag bestimmt das Subgenre, Audio überschreibt nur die Achsen.
    res = classify_track(170.0, tags=["tribal"], lra=1.5, breakdown_ratio=0.4)
    assert res.subgenre == Subgenre.TRIBAL
    assert res.flow == FlowAxis.VERTICAL


def test_breakdown_schwelle_grenzwerte():
    # Genau auf der Schwelle -> vertikal, knapp darunter -> horizontal.
    assert classify_track(150.0, breakdown_ratio=0.20).flow == FlowAxis.VERTICAL
    assert classify_track(150.0, breakdown_ratio=0.199).flow == FlowAxis.HORIZONTAL


def test_chroma_schwelle_grenzwerte():
    assert classify_track(150.0, chroma_dominance=0.60).aesthetic == Aesthetic.HARMONIC_MELODIC
    assert classify_track(150.0, chroma_dominance=0.59).aesthetic == Aesthetic.PERCUSSIVE_ATONAL


def test_terror_braucht_geringe_lra():
    # Schnell, aber dynamisch -> kein Terror.
    res = classify_track(170.0, lra=8.0)
    assert res.subgenre != Subgenre.TERROR


def test_terror_ohne_lra_ist_kein_terror():
    res = classify_track(170.0)
    assert res.subgenre != Subgenre.TERROR


def test_version_type_schlaegt_subgenre_detektion():
    res = classify_track(150.0, title="Krenon - Impact (DJ Tool)")
    assert res.version_type == VersionType.TOOL


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Artist - Track (VIP Dub)", VersionType.VIP),
        ("Artist - Track [Rework]", VersionType.REWORK),
        ("Artist - Track (Official Remix)", VersionType.REMIX),
        ("Something bootleg", VersionType.BOOTLEG),
        ("Edit Me", VersionType.EDIT),
        ("Mashup Madness", VersionType.MASHUP),
        ("Flip It", VersionType.FLIP),
        ("Plain Song", VersionType.ORIGINAL),
        ("", VersionType.ORIGINAL),
    ],
)
def test_version_type_titelmuster(title, expected):
    assert detect_version_type(title) == expected


def test_version_type_tag_variante():
    assert detect_version_type("Track", ["Bootleg"]) == VersionType.BOOTLEG
    assert detect_version_type("Track", ["rework"]) == VersionType.REWORK
    assert detect_version_type("Track", ["tool"]) == VersionType.TOOL


def test_version_type_original_mix_ist_kein_remix():
    assert detect_version_type("Artist - Song (Original Mix)") == VersionType.ORIGINAL


def test_energy_ist_immer_im_bereich():
    for bpm in (None, 60.0, 120.0, 145.0, 160.0, 200.0, 300.0):
        value = calculate_energy(bpm, lufs=-8.0, lra=5.0, subgenre=Subgenre.INDUSTRIAL)
        assert 1 <= value <= 5


def test_energy_terror_und_crossbreed_heben_an():
    # Bei gleichem Tempo liegt Crossbreed eine Stufe über Classic.
    assert calculate_energy(158.0, subgenre=Subgenre.CLASSIC) == 4
    assert calculate_energy(158.0, subgenre=Subgenre.CROSSBREED) == 5
    assert calculate_energy(158.0, subgenre=Subgenre.MELODIC) == 3


def test_rekordbox_farbe_unbekannter_typ_ist_none():
    analysis = _analysis(Subgenre.CLASSIC, VersionType.ORIGINAL)
    assert recommend_rekordbox_color(analysis) == "None"
    analysis.version_type = VersionType.REMIX
    assert recommend_rekordbox_color(analysis) == "None"


def test_format_original_versus_version_label():
    analysis = _analysis(Subgenre.REVERSE_BASS, VersionType.ORIGINAL)
    assert format_rekordbox_comment(analysis, bpm=152.0).startswith("[Reverse Bass] 152 BPM")
    analysis.version_type = VersionType.VIP
    assert format_rekordbox_comment(analysis, bpm=152.0).startswith("[VIP] 152 BPM")


def test_format_ohne_flow_und_ohne_key():
    analysis = _analysis(Subgenre.TRIBAL, VersionType.TOOL)
    assert format_rekordbox_comment(analysis, bpm=150.0, include_flow=False) == "[TOOL] 150 BPM | Notiz"


def test_format_ganzzahlige_bpm_ohne_nachkomma():
    analysis = _analysis(Subgenre.BOUNCE, VersionType.ORIGINAL)
    comment = format_rekordbox_comment(analysis, bpm=154.0, key_camelot="5A")
    assert "154 BPM" in comment
    assert "154.0" not in comment


def test_classify_track_default_note_nie_leer():
    res = classify_track(None)
    assert res.dj_note
    assert res.confidence <= 0.6
