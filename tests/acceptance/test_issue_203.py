"""Akzeptanztests: Die Schranz-Matrix – Modul sc_digger/style.py."""
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


def test_enums_und_dataclass_vollstaendigkeit():
    # 4 Cluster
    assert set(StyleCluster) == {
        StyleCluster.FUNDAMENT,
        StyleCluster.NOISE,
        StyleCluster.EMOTION,
        StyleCluster.CROSSOVER,
    }
    # 13 Subgenres
    assert len(Subgenre) == 13
    assert set(Subgenre) == {
        Subgenre.TRIBAL,
        Subgenre.HARDGROOVE,
        Subgenre.CLASSIC,
        Subgenre.INDUSTRIAL,
        Subgenre.ACID,
        Subgenre.TERROR,
        Subgenre.NEO,
        Subgenre.MELODIC,
        Subgenre.BOUNCE,
        Subgenre.PSY,
        Subgenre.REVERSE_BASS,
        Subgenre.EBM,
        Subgenre.CROSSBREED,
    }
    # 3 Achsen
    assert set(FlowAxis) == {FlowAxis.HORIZONTAL, FlowAxis.VERTICAL}
    assert set(RhythmRaster) == {
        RhythmRaster.STRAIGHT_4_4,
        RhythmRaster.SYNCOPATED,
        RhythmRaster.OFFBEAT_PUMP,
        RhythmRaster.TRIPLET,
        RhythmRaster.BREAKBEAT,
    }
    assert set(Aesthetic) == {
        Aesthetic.PERCUSSIVE_ATONAL,
        Aesthetic.HARMONIC_MELODIC,
    }
    # 9 Versionstypen
    assert len(VersionType) == 9
    assert set(VersionType) == {
        VersionType.EDIT,
        VersionType.BOOTLEG,
        VersionType.REMIX,
        VersionType.MASHUP,
        VersionType.FLIP,
        VersionType.VIP,
        VersionType.REWORK,
        VersionType.TOOL,
        VersionType.ORIGINAL,
    }


def test_classify_track_alle_13_subgenres():
    erwartungen = [
        # (tags, title, bpm, expected_subgenre, expected_cluster, expected_flow, expected_raster, expected_aesthetic)
        (["tribal"], None, 142.0, Subgenre.TRIBAL, StyleCluster.FUNDAMENT, FlowAxis.HORIZONTAL, RhythmRaster.SYNCOPATED, Aesthetic.PERCUSSIVE_ATONAL),
        (["hardgroove"], None, 146.0, Subgenre.HARDGROOVE, StyleCluster.FUNDAMENT, FlowAxis.HORIZONTAL, RhythmRaster.SYNCOPATED, Aesthetic.PERCUSSIVE_ATONAL),
        (["classic"], None, 150.0, Subgenre.CLASSIC, StyleCluster.FUNDAMENT, FlowAxis.HORIZONTAL, RhythmRaster.STRAIGHT_4_4, Aesthetic.PERCUSSIVE_ATONAL),
        (["industrial"], None, 155.0, Subgenre.INDUSTRIAL, StyleCluster.NOISE, FlowAxis.VERTICAL, RhythmRaster.STRAIGHT_4_4, Aesthetic.PERCUSSIVE_ATONAL),
        (["acid", "303"], None, 154.0, Subgenre.ACID, StyleCluster.NOISE, FlowAxis.HORIZONTAL, RhythmRaster.STRAIGHT_4_4, Aesthetic.HARMONIC_MELODIC),
        (["terror"], None, 168.0, Subgenre.TERROR, StyleCluster.NOISE, FlowAxis.VERTICAL, RhythmRaster.STRAIGHT_4_4, Aesthetic.PERCUSSIVE_ATONAL),
        (["neo"], None, 155.0, Subgenre.NEO, StyleCluster.EMOTION, FlowAxis.VERTICAL, RhythmRaster.STRAIGHT_4_4, Aesthetic.PERCUSSIVE_ATONAL),
        (["melodic"], None, 152.0, Subgenre.MELODIC, StyleCluster.EMOTION, FlowAxis.VERTICAL, RhythmRaster.STRAIGHT_4_4, Aesthetic.HARMONIC_MELODIC),
        (["bounce"], "Donk Anthem", 154.0, Subgenre.BOUNCE, StyleCluster.CROSSOVER, FlowAxis.VERTICAL, RhythmRaster.OFFBEAT_PUMP, Aesthetic.PERCUSSIVE_ATONAL),
        (["psy", "triplet"], None, 155.0, Subgenre.PSY, StyleCluster.CROSSOVER, FlowAxis.VERTICAL, RhythmRaster.TRIPLET, Aesthetic.PERCUSSIVE_ATONAL),
        (["reverse_bass"], None, 152.0, Subgenre.REVERSE_BASS, StyleCluster.CROSSOVER, FlowAxis.VERTICAL, RhythmRaster.OFFBEAT_PUMP, Aesthetic.PERCUSSIVE_ATONAL),
        (["ebm"], None, 150.0, Subgenre.EBM, StyleCluster.CROSSOVER, FlowAxis.HORIZONTAL, RhythmRaster.STRAIGHT_4_4, Aesthetic.HARMONIC_MELODIC),
        (["breakcore", "amen"], None, 160.0, Subgenre.CROSSBREED, StyleCluster.CROSSOVER, FlowAxis.VERTICAL, RhythmRaster.BREAKBEAT, Aesthetic.PERCUSSIVE_ATONAL),
    ]

    for tags, title, bpm, exp_sub, exp_cluster, exp_flow, exp_raster, exp_aes in erwartungen:
        res = classify_track(bpm, tags=tags, title=title)
        assert res.subgenre == exp_sub, f"Erwartete {exp_sub} fuer tags={tags}, got {res.subgenre}"
        assert res.cluster == exp_cluster
        assert res.flow == exp_flow
        assert res.raster == exp_raster
        assert res.aesthetic == exp_aes
        assert 1 <= res.energy <= 5
        assert isinstance(res.dj_note, str) and len(res.dj_note) > 0
        assert 0.0 <= res.confidence <= 1.0
        assert res.version_type == VersionType.ORIGINAL


def test_classify_track_audio_features_ueberschreiben_achsen():
    # Hohe Breakdown-Ratio schaltet auf Vertikal um
    res_vert = classify_track(145.0, tags=["tribal"], breakdown_ratio=0.25)
    assert res_vert.subgenre == Subgenre.TRIBAL
    assert res_vert.flow == FlowAxis.VERTICAL

    # Geringe Breakdown-Ratio schaltet auf Horizontal
    res_horiz = classify_track(155.0, tags=["industrial"], breakdown_ratio=0.08)
    assert res_horiz.subgenre == Subgenre.INDUSTRIAL
    assert res_horiz.flow == FlowAxis.HORIZONTAL

    # Hohe Chroma-Dominanz schaltet auf Harmonic/Melodic um
    res_chroma = classify_track(150.0, tags=["classic"], chroma_dominance=0.75)
    assert res_chroma.subgenre == Subgenre.CLASSIC
    assert res_chroma.aesthetic == Aesthetic.HARMONIC_MELODIC


def test_classify_track_audio_merkmale_erkennung_ohne_tags():
    # Terror via Tempo >= 165 und sehr geringe LRA
    res_terror = classify_track(170.0, lra=2.0)
    assert res_terror.subgenre == Subgenre.TERROR
    assert res_terror.cluster == StyleCluster.NOISE

    # Melodic via hohes Chroma und lange Breaks
    res_melodic = classify_track(154.0, breakdown_ratio=0.35, chroma_dominance=0.65)
    assert res_melodic.subgenre == Subgenre.MELODIC
    assert res_melodic.cluster == StyleCluster.EMOTION


def test_classify_track_fallbacks():
    # Ohne Angaben: Default Klassischer Schranz
    res_empty = classify_track(None)
    assert res_empty.subgenre == Subgenre.CLASSIC
    assert res_empty.cluster == StyleCluster.FUNDAMENT
    assert res_empty.flow == FlowAxis.HORIZONTAL
    assert res_empty.confidence <= 0.6

    # Hohe Breakdown-Ratio ohne Tags: Default Neo-Schranz
    res_neo = classify_track(155.0, breakdown_ratio=0.22)
    assert res_neo.subgenre == Subgenre.NEO
    assert res_neo.cluster == StyleCluster.EMOTION
    assert res_neo.flow == FlowAxis.VERTICAL

    # Schnelles Tempo ohne Tags: Default Neo-Schranz
    res_fast = classify_track(156.0)
    assert res_fast.subgenre == Subgenre.NEO

    # Moderates Tempo ohne Breakdown-Ratio: Default Klassischer Schranz
    res_classic = classify_track(148.0)
    assert res_classic.subgenre == Subgenre.CLASSIC


def test_calculate_energy_stufen():
    # Stufe 1: langsam und leise/dynamisch
    assert calculate_energy(132.0, lufs=-14.0, subgenre=Subgenre.TRIBAL) == 1

    # Stufe 2: moderates Tempo, Tribal / EBM
    assert calculate_energy(140.0, lufs=-9.5, subgenre=Subgenre.TRIBAL) == 2

    # Stufe 3: klassischer Schranz, Standardpegel
    assert calculate_energy(148.0, lufs=-8.0, subgenre=Subgenre.CLASSIC) == 3

    # Stufe 4: modernes Tempo, lauter Pegel
    assert calculate_energy(155.0, lufs=-6.0, subgenre=Subgenre.BOUNCE) == 4

    # Stufe 5: Extremtempo / Terror / Brickwall
    assert calculate_energy(168.0, lufs=-4.5, lra=1.8, subgenre=Subgenre.TERROR) == 5

    # Bounds: immer zwischen 1 und 5
    assert calculate_energy(None) == 3
    assert calculate_energy(200.0, lufs=-1.0, lra=0.5, subgenre=Subgenre.TERROR) == 5
    assert calculate_energy(100.0, lufs=-30.0, lra=15.0, subgenre=Subgenre.TRIBAL) == 1


def test_detect_version_type_alle_varianten():
    faelle = [
        ("Krenon - Impact (DJ Tool)", [], VersionType.TOOL),
        ("Bass Blaster - Rave (Loop Tool)", [], VersionType.TOOL),
        ("Artist - Anthem (VIP)", [], VersionType.VIP),
        ("Artist - Anthem (VIP Mix)", [], VersionType.VIP),
        ("Madonna - Hung Up (Hardtechno Bootleg)", [], VersionType.BOOTLEG),
        ("Track Title", ["bootleg"], VersionType.BOOTLEG),
        ("Acapella vs Beat (Mashup)", [], VersionType.MASHUP),
        ("Classic - Tune (Jersey Flip)", [], VersionType.FLIP),
        ("Oldie - Hit (2024 Rework)", [], VersionType.REWORK),
        ("Producer - Track (DJ Edit)", [], VersionType.EDIT),
        ("Producer - Track (Re-Edit)", [], VersionType.EDIT),
        ("Artist - Song (Remix)", [], VersionType.REMIX),
        ("Artist - Song (Original Mix)", [], VersionType.ORIGINAL),
        ("Regular Track Title", None, VersionType.ORIGINAL),
        (None, None, VersionType.ORIGINAL),
    ]
    for title, tags, expected in faelle:
        assert detect_version_type(title, tags) == expected


def test_recommend_rekordbox_color():
    base = StyleAnalysis(
        cluster=StyleCluster.FUNDAMENT,
        subgenre=Subgenre.CLASSIC,
        flow=FlowAxis.HORIZONTAL,
        raster=RhythmRaster.STRAIGHT_4_4,
        aesthetic=Aesthetic.PERCUSSIVE_ATONAL,
        energy=3,
        dj_note="",
        confidence=1.0,
    )

    base.version_type = VersionType.TOOL
    assert recommend_rekordbox_color(base) == "Blue"

    base.version_type = VersionType.EDIT
    assert recommend_rekordbox_color(base) == "Green"

    for vt in (VersionType.BOOTLEG, VersionType.MASHUP, VersionType.FLIP):
        base.version_type = vt
        assert recommend_rekordbox_color(base) == "Red"

    for vt in (VersionType.VIP, VersionType.REWORK):
        base.version_type = vt
        assert recommend_rekordbox_color(base) == "Yellow"

    for vt in (VersionType.ORIGINAL, VersionType.REMIX):
        base.version_type = vt
        assert recommend_rekordbox_color(base) == "None"


def test_format_rekordbox_comment_original_beispiele():
    # Bounce Original mit BPM und Key
    bounce = StyleAnalysis(
        cluster=StyleCluster.CROSSOVER,
        subgenre=Subgenre.BOUNCE,
        flow=FlowAxis.VERTICAL,
        raster=RhythmRaster.OFFBEAT_PUMP,
        aesthetic=Aesthetic.PERCUSSIVE_ATONAL,
        energy=4,
        dj_note="⚠️ Subbass-Phasengefahr!",
        confidence=0.9,
        version_type=VersionType.ORIGINAL,
    )
    assert format_rekordbox_comment(bounce, bpm=154.0, key_camelot="5A") == (
        "[Bounce] 154 BPM · 5A | Vertikal · ⚠️ Subbass-Phasengefahr!"
    )

    # Tribal Original
    tribal = StyleAnalysis(
        cluster=StyleCluster.FUNDAMENT,
        subgenre=Subgenre.TRIBAL,
        flow=FlowAxis.HORIZONTAL,
        raster=RhythmRaster.SYNCOPATED,
        aesthetic=Aesthetic.PERCUSSIVE_ATONAL,
        energy=2,
        dj_note="3-Deck Loop Tool",
        confidence=0.85,
        version_type=VersionType.ORIGINAL,
    )
    assert format_rekordbox_comment(tribal, bpm=142.0, key_camelot="11A") == (
        "[Tribal] 142 BPM · 11A | Horizontal · 3-Deck Loop Tool"
    )

    # Minimal (ohne BPM/Key)
    assert format_rekordbox_comment(bounce) == "[Bounce] | Vertikal · ⚠️ Subbass-Phasengefahr!"


def test_format_rekordbox_comment_version_types():
    # TOOL
    tool = StyleAnalysis(
        cluster=StyleCluster.FUNDAMENT,
        subgenre=Subgenre.TRIBAL,
        flow=FlowAxis.HORIZONTAL,
        raster=RhythmRaster.SYNCOPATED,
        aesthetic=Aesthetic.PERCUSSIVE_ATONAL,
        energy=3,
        dj_note="3-Deck Loop Tool",
        confidence=0.9,
        version_type=VersionType.TOOL,
    )
    assert format_rekordbox_comment(tool, bpm=150.0, key_camelot="5A", include_flow=False) == (
        "[TOOL] 150 BPM · 5A | 3-Deck Loop Tool"
    )

    # BOOTLEG
    bootleg = StyleAnalysis(
        cluster=StyleCluster.EMOTION,
        subgenre=Subgenre.NEO,
        flow=FlowAxis.VERTICAL,
        raster=RhythmRaster.STRAIGHT_4_4,
        aesthetic=Aesthetic.PERCUSSIVE_ATONAL,
        energy=4,
        dj_note="⚠️ Vocal Hook / Crowd Peak",
        confidence=0.9,
        version_type=VersionType.BOOTLEG,
    )
    assert format_rekordbox_comment(bootleg, bpm=155.0, key_camelot="8A", include_flow=False) == (
        "[BOOTLEG] 155 BPM · 8A | ⚠️ Vocal Hook / Crowd Peak"
    )

    # EDIT
    edit = StyleAnalysis(
        cluster=StyleCluster.FUNDAMENT,
        subgenre=Subgenre.HARDGROOVE,
        flow=FlowAxis.HORIZONTAL,
        raster=RhythmRaster.SYNCOPATED,
        aesthetic=Aesthetic.PERCUSSIVE_ATONAL,
        energy=3,
        dj_note="Ext. Intro/Outro",
        confidence=0.9,
        version_type=VersionType.EDIT,
    )
    assert format_rekordbox_comment(edit, bpm=152.0, key_camelot="2A", include_flow=False) == (
        "[EDIT] 152 BPM · 2A | Ext. Intro/Outro"
    )
