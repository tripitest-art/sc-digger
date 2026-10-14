"""Die Schranz-Matrix: Stil-, Achsen-, Versions- und Energie-Klassifizierung.

Schranz-Tracks sind funktionale DJ-Tools. Dieses Modul ordnet sie genre-relativ in
4 Hauptcluster und 13 Subgenres ein, beschreibt sie entlang von 3 stilistischen Achsen
(Fluss, Metrum/Raster, Klangästhetik), erkennt den funktionalen Versionstyp (Edit,
Bootleg, Tool, …) und leitet daraus Rekordbox-Empfehlungen (Farbe, Kommentar) ab.

Alles ist regel- und merkmal-basiert und liefert *immer* ein gültiges Ergebnis:
Tags schlagen Audio-Merkmale (nur die Achsen werden überschrieben), fehlen alle
Anhaltspunkte, greift ein konservativer Fallback (Classic vs. Neo).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Sequence


class StyleCluster(str, Enum):
    """Die 4 Hauptcluster der Schranz-Matrix."""
    FUNDAMENT = "fundament"
    NOISE = "noise"
    EMOTION = "emotion"
    CROSSOVER = "crossover"


class Subgenre(str, Enum):
    """Die 13 Subgenres der Schranz-Matrix."""
    # Cluster 1: Fundament & Tools
    TRIBAL = "tribal"
    HARDGROOVE = "hardgroove"
    CLASSIC = "classic"
    # Cluster 2: Noise & Extremhärte
    INDUSTRIAL = "industrial"
    ACID = "acid"
    TERROR = "terror"
    # Cluster 3: Emotion & Dramaturgie
    NEO = "neo"
    MELODIC = "melodic"
    # Cluster 4: Rhythmus-Crossover
    BOUNCE = "bounce"
    PSY = "psy"
    REVERSE_BASS = "reverse_bass"
    EBM = "ebm"
    CROSSBREED = "crossbreed"


class FlowAxis(str, Enum):
    """Dramaturgische Fluss-Achse."""
    HORIZONTAL = "horizontal"  # Linearer Fluss, minimale Breaks, Filter-Progression
    VERTICAL = "vertical"      # Stopp & Drop, Dynamiksprünge, theatralische Breaks


class RhythmRaster(str, Enum):
    """Rhythmisches Raster / Metrum."""
    STRAIGHT_4_4 = "straight_4_4"
    SYNCOPATED = "syncopated"
    OFFBEAT_PUMP = "offbeat_pump"
    TRIPLET = "triplet"
    BREAKBEAT = "breakbeat"


class Aesthetic(str, Enum):
    """Klangästhetik / Spektrale Tönung."""
    PERCUSSIVE_ATONAL = "percussive_atonal"
    HARMONIC_MELODIC = "harmonic_melodic"


class VersionType(str, Enum):
    """Funktionaler Versionstyp eines Tracks für DJs."""
    EDIT = "edit"          # DJ Edit / Re-Edit (Mix-optimiert)
    BOOTLEG = "bootleg"    # Inoffizieller Remix (bekannte Hook in neuem Genre)
    REMIX = "remix"        # Offizieller Remix
    MASHUP = "mashup"      # Kombination zweier Tracks (Acapella + Instrumental)
    FLIP = "flip"          # Sample zweckentfremdet / Genre-Clash
    VIP = "vip"            # Set-Exklusivität des Produzenten (Variation in Production)
    REWORK = "rework"      # Rework / Tribute (neu eingespielt)
    TOOL = "tool"          # Rhythmisches Füllmaterial für Mehrdeck-Sets
    ORIGINAL = "original"  # Reguläres Original


@dataclass
class StyleAnalysis:
    """Ergebnis der stilistischen Track-Analyse."""
    cluster: StyleCluster
    subgenre: Subgenre
    flow: FlowAxis
    raster: RhythmRaster
    aesthetic: Aesthetic
    energy: int  # 1..5
    dj_note: str
    confidence: float  # 0.0 .. 1.0
    version_type: VersionType = VersionType.ORIGINAL


# --- Versionstypen ---------------------------------------------------------
# Reihenfolge = Priorität. EDIT zuerst, weil "DJ Edit"/"Re-Edit" sonst an anderen
# Mustern (z. B. "Tool") vorbeigingen; ORIGINAL zuletzt als explizites Signal.
_VERSION_PATTERNS: list[tuple[VersionType, re.Pattern[str]]] = [
    (VersionType.EDIT, re.compile(r"\b(?:re-?edit|edit)\b", re.I)),
    (VersionType.BOOTLEG, re.compile(r"\bbootleg\b", re.I)),
    (VersionType.MASHUP, re.compile(r"\bmashup\b", re.I)),
    (VersionType.FLIP, re.compile(r"\bflip\b", re.I)),
    (VersionType.VIP, re.compile(r"\bvip\b", re.I)),
    (VersionType.REWORK, re.compile(r"\brework\b", re.I)),
    (VersionType.TOOL, re.compile(r"\btool\b", re.I)),
    (VersionType.REMIX, re.compile(r"\bremix\b", re.I)),
    (VersionType.ORIGINAL, re.compile(r"\boriginal\b", re.I)),
]


def detect_version_type(title: str | None, tags: Sequence[str] | None = None) -> VersionType:
    """Erkennt den funktionalen Versionstyp anhand von Titel und Tags per Regex. Default: ORIGINAL."""
    parts: list[str] = []
    if title:
        parts.append(str(title))
    if tags:
        parts.extend(str(t) for t in tags)
    haystack = " ".join(parts)
    if not haystack.strip():
        return VersionType.ORIGINAL
    for version_type, pattern in _VERSION_PATTERNS:
        if pattern.search(haystack):
            return version_type
    return VersionType.ORIGINAL


# Farbcodierung nach der Schranz-Matrix (Subgenre). Anders als die Versionstyp-Farben
# beschreibt sie das Klangbild, nicht die Funktion des Tracks; deshalb zuschaltbar
# (rekordbox.matrix_colors) und mit Vorrang vor style_colors.
_MATRIX_COLORS: dict[Subgenre, str] = {
    Subgenre.INDUSTRIAL: "Red",
    Subgenre.TERROR: "Red",
    Subgenre.CROSSBREED: "Red",
    Subgenre.CLASSIC: "Blue",
    Subgenre.TRIBAL: "Green",
    Subgenre.HARDGROOVE: "Green",
    Subgenre.MELODIC: "Yellow",
    Subgenre.NEO: "Orange",
    Subgenre.BOUNCE: "Pink",
    Subgenre.ACID: "Purple",
    Subgenre.PSY: "Purple",
    Subgenre.REVERSE_BASS: "Purple",
    Subgenre.EBM: "Purple",
}


def recommend_rekordbox_color(analysis: StyleAnalysis, *, by_matrix: bool = False) -> str:
    """Gibt eine Rekordbox-Farbempfehlung zurück.

    by_matrix=False (Standard): Farbe nach Versionstyp.
    TOOL -> 'Blue', EDIT -> 'Green', BOOTLEG/MASHUP/FLIP -> 'Red', VIP/REWORK -> 'Yellow', sonst 'None'.
    by_matrix=True: Farbe nach Subgenre (Schranz-Matrix), z. B. Industrial/Terror/Crossbreed -> 'Red',
    Classic -> 'Blue', Tribal/Hardgroove -> 'Green', Melodic -> 'Yellow', Neo -> 'Orange',
    Bounce -> 'Pink', Acid/Psy/Reverse Bass/EBM -> 'Purple'."""
    if by_matrix:
        return _MATRIX_COLORS.get(analysis.subgenre, "None")
    mapping = {
        VersionType.TOOL: "Blue",
        VersionType.EDIT: "Green",
        VersionType.BOOTLEG: "Red",
        VersionType.MASHUP: "Red",
        VersionType.FLIP: "Red",
        VersionType.VIP: "Yellow",
        VersionType.REWORK: "Yellow",
    }
    return mapping.get(analysis.version_type, "None")


# Schwellwerte der Energie-Berechnung (calculate_energy).
_ENERGY_LUFS_LOUD = -6.0          # lauter als das -> +1
_ENERGY_LRA_BRICKWALL = 3.0       # weniger Dynamik (Brickwall) -> +1
_ENERGY_LRA_DYNAMIC = 6.5         # mehr Dynamik (Minimal) -> -1
_ENERGY_CENTROID_BRIGHT = 3500.0  # hell / Distortion / Noise -> +1
_ENERGY_CENTROID_DARK = 1800.0    # dunkel / warmer Sub -> -1


def calculate_energy(
    bpm: float | None,
    lufs: float | None = None,
    lra: float | None = None,
    subgenre: Subgenre | None = None,
    *,
    spectral_centroid_hz: float | None = None,
    rms: float | None = None,
) -> int:
    """Berechnet ein Energie-Level von 1 bis 5.

    Das Tempo bestimmt die Grundstufe, lauter Pegel, geringe Dynamik (Brickwall),
    ein heller/verzerrter Spektral-Schwerpunkt und harte Subgenres heben an,
    melodische/entspannte oder sehr dynamische (Minimal) senken leicht. Am Ende immer 1..5.
    `rms` wird derzeit nur entgegengenommen, fließt aber nicht in die Bewertung ein.
    """
    if bpm is None:
        base = 3
    elif bpm < 138:
        base = 1
    elif bpm < 145:
        base = 2
    elif bpm < 152:
        base = 3
    elif bpm < 160:
        base = 4
    else:
        base = 5

    score = base
    if lufs is not None and lufs > _ENERGY_LUFS_LOUD:
        score += 1
    if lra is not None and lra < _ENERGY_LRA_BRICKWALL:
        score += 1
    if lra is not None and lra > _ENERGY_LRA_DYNAMIC:
        score -= 1
    if spectral_centroid_hz is not None and spectral_centroid_hz > _ENERGY_CENTROID_BRIGHT:
        score += 1
    if spectral_centroid_hz is not None and spectral_centroid_hz < _ENERGY_CENTROID_DARK:
        score -= 1
    if subgenre in (Subgenre.TERROR, Subgenre.CROSSBREED):
        score += 1
    if subgenre in (Subgenre.MELODIC, Subgenre.EBM):
        score -= 1

    return max(1, min(5, score))


# --- Subgenre-Profile ------------------------------------------------------
# Feste stilistische Signatur je Subgenre: Cluster + die drei Achsen.
_SUBGENRE_PROFILE: dict[Subgenre, tuple[StyleCluster, FlowAxis, RhythmRaster, Aesthetic]] = {
    Subgenre.TRIBAL: (StyleCluster.FUNDAMENT, FlowAxis.HORIZONTAL, RhythmRaster.SYNCOPATED, Aesthetic.PERCUSSIVE_ATONAL),
    Subgenre.HARDGROOVE: (StyleCluster.FUNDAMENT, FlowAxis.HORIZONTAL, RhythmRaster.SYNCOPATED, Aesthetic.PERCUSSIVE_ATONAL),
    Subgenre.CLASSIC: (StyleCluster.FUNDAMENT, FlowAxis.HORIZONTAL, RhythmRaster.STRAIGHT_4_4, Aesthetic.PERCUSSIVE_ATONAL),
    Subgenre.INDUSTRIAL: (StyleCluster.NOISE, FlowAxis.VERTICAL, RhythmRaster.STRAIGHT_4_4, Aesthetic.PERCUSSIVE_ATONAL),
    Subgenre.ACID: (StyleCluster.NOISE, FlowAxis.HORIZONTAL, RhythmRaster.STRAIGHT_4_4, Aesthetic.HARMONIC_MELODIC),
    Subgenre.TERROR: (StyleCluster.NOISE, FlowAxis.VERTICAL, RhythmRaster.STRAIGHT_4_4, Aesthetic.PERCUSSIVE_ATONAL),
    Subgenre.NEO: (StyleCluster.EMOTION, FlowAxis.VERTICAL, RhythmRaster.STRAIGHT_4_4, Aesthetic.PERCUSSIVE_ATONAL),
    Subgenre.MELODIC: (StyleCluster.EMOTION, FlowAxis.VERTICAL, RhythmRaster.STRAIGHT_4_4, Aesthetic.HARMONIC_MELODIC),
    Subgenre.BOUNCE: (StyleCluster.CROSSOVER, FlowAxis.VERTICAL, RhythmRaster.OFFBEAT_PUMP, Aesthetic.PERCUSSIVE_ATONAL),
    Subgenre.PSY: (StyleCluster.CROSSOVER, FlowAxis.VERTICAL, RhythmRaster.TRIPLET, Aesthetic.PERCUSSIVE_ATONAL),
    Subgenre.REVERSE_BASS: (StyleCluster.CROSSOVER, FlowAxis.VERTICAL, RhythmRaster.OFFBEAT_PUMP, Aesthetic.PERCUSSIVE_ATONAL),
    Subgenre.EBM: (StyleCluster.CROSSOVER, FlowAxis.HORIZONTAL, RhythmRaster.STRAIGHT_4_4, Aesthetic.HARMONIC_MELODIC),
    Subgenre.CROSSBREED: (StyleCluster.CROSSOVER, FlowAxis.VERTICAL, RhythmRaster.BREAKBEAT, Aesthetic.PERCUSSIVE_ATONAL),
}

# DJ-Notiz je Subgenre. Nicht leer, gibt dem DJ in Rekordbox einen Hinweis.
_DJ_NOTES: dict[Subgenre, str] = {
    Subgenre.TRIBAL: "3-Deck Loop Tool",
    Subgenre.HARDGROOVE: "Ext. Intro/Outro",
    Subgenre.CLASSIC: "Klassischer Schranz · 4/4",
    Subgenre.INDUSTRIAL: "⚠️ Noise-Layer / Hall",
    Subgenre.ACID: "303-Line · Filter fahren",
    Subgenre.TERROR: "⚠️ Extremtempo!",
    Subgenre.NEO: "⚠️ Stopp & Drop",
    Subgenre.MELODIC: "Melodische Hook / Peak",
    Subgenre.BOUNCE: "⚠️ Subbass-Phasengefahr!",
    Subgenre.PSY: "Triolen-Groove · Hypnose",
    Subgenre.REVERSE_BASS: "Reverse-Bass · Offbeat",
    Subgenre.EBM: "EBM-Bassline · düster",
    Subgenre.CROSSBREED: "Breakbeat · DnB-Kante",
}

# Stichwort -> Subgenre. Reihenfolge = Priorität innerhalb der Tags.
_KEYWORD_TO_SUBGENRE: list[tuple[str, Subgenre]] = [
    ("reverse_bass", Subgenre.REVERSE_BASS),
    ("reversebass", Subgenre.REVERSE_BASS),
    ("reverse bass", Subgenre.REVERSE_BASS),
    ("crossbreed", Subgenre.CROSSBREED),
    ("breakcore", Subgenre.CROSSBREED),
    ("amen", Subgenre.CROSSBREED),
    ("hardgroove", Subgenre.HARDGROOVE),
    ("hard groove", Subgenre.HARDGROOVE),
    ("industrial", Subgenre.INDUSTRIAL),
    ("tribal", Subgenre.TRIBAL),
    ("classic", Subgenre.CLASSIC),
    ("acid", Subgenre.ACID),
    ("303", Subgenre.ACID),
    ("terror", Subgenre.TERROR),
    ("melodic", Subgenre.MELODIC),
    ("neo", Subgenre.NEO),
    ("bounce", Subgenre.BOUNCE),
    ("donk", Subgenre.BOUNCE),
    ("psy", Subgenre.PSY),
    ("triplet", Subgenre.PSY),
    ("ebm", Subgenre.EBM),
]

# Schwellwerte für die Audio-Merkmal-Überschreibung der Achsen.
_BREAKDOWN_VERTICAL = 0.20   # ab hier theatralisch -> Vertikal
_CHROMA_HARMONIC = 0.60      # ab hier tonal/melodisch
_MELODIC_BREAKDOWN = 0.30    # lange Breaks + Chroma -> Melodic
_TERROR_BPM = 165.0          # Extremtempo
_BRICKWALL_LRA = 3.0         # sehr geringe Dynamik


def _normalize(text: str) -> str:
    return text.strip().lower().replace("-", " ").replace("_", " ")


def _match_subgenre_by_keyword(tags: Sequence[str] | None, title: str | None) -> Subgenre | None:
    """Sucht in Tags (zuerst) und Titel nach einem bekannten Stichwort."""
    sources: list[str] = []
    if tags:
        sources.extend(str(t) for t in tags)
    if title:
        sources.append(str(title))
    for source in sources:
        haystack = _normalize(source)
        for keyword, subgenre in _KEYWORD_TO_SUBGENRE:
            if keyword in haystack:
                return subgenre
    return None


def _detect_subgenre_audio(
    bpm: float | None,
    lra: float | None,
    breakdown_ratio: float | None,
    chroma_dominance: float | None,
) -> Subgenre | None:
    """Leitet aus reinen Audio-Merkmalen ein Subgenre ab, wenn keine Tags vorliegen."""
    # Melodische Dramaturgie: lange Breaks UND tonale Dominanz.
    if (
        breakdown_ratio is not None
        and breakdown_ratio >= _MELODIC_BREAKDOWN
        and chroma_dominance is not None
        and chroma_dominance >= _CHROMA_HARMONIC
    ):
        return Subgenre.MELODIC
    # Extremtempo mit zermalmter Dynamik -> Terror.
    if bpm is not None and bpm >= _TERROR_BPM and lra is not None and lra <= _BRICKWALL_LRA:
        return Subgenre.TERROR
    # Theatralische Breaks oder hohes Tempo -> Neo.
    if (breakdown_ratio is not None and breakdown_ratio >= _BREAKDOWN_VERTICAL) or (
        bpm is not None and bpm >= 155.0
    ):
        return Subgenre.NEO
    return None


def classify_track(
    bpm: float | None,
    *,
    tags: Sequence[str] | None = None,
    title: str | None = None,
    lra: float | None = None,
    breakdown_ratio: float | None = None,
    chroma_dominance: float | None = None,
    spectral_centroid_hz: float | None = None,
    rms: float | None = None,
) -> StyleAnalysis:
    """Klassifiziert einen Track anhand von BPM, Metadaten und Audio-Merkmalen. Liefert immer ein gültiges StyleAnalysis.

    Priorität: Tags/Titel bestimmen das Subgenre; Audio-Merkmale überschreiben nur die
    Achsen (Fluss/Ästhetik). Ohne Tags versucht die Erkennung, aus den Audio-Merkmalen
    ein Subgenre abzuleiten; bleibt nichts übrig, greift der Fallback Classic/Neo.
    """
    matched = _match_subgenre_by_keyword(tags, title)
    from_tags = matched is not None
    if matched is None:
        matched = _detect_subgenre_audio(bpm, lra, breakdown_ratio, chroma_dominance)
    from_audio = matched is not None and not from_tags
    if matched is None:
        matched = Subgenre.CLASSIC

    cluster, flow, raster, aesthetic = _SUBGENRE_PROFILE[matched]

    # Audio-Merkmale überschreiben die Achsen, nie das Subgenre.
    if breakdown_ratio is not None:
        flow = FlowAxis.VERTICAL if breakdown_ratio >= _BREAKDOWN_VERTICAL else FlowAxis.HORIZONTAL
    if chroma_dominance is not None:
        aesthetic = (
            Aesthetic.HARMONIC_MELODIC
            if chroma_dominance >= _CHROMA_HARMONIC
            else Aesthetic.PERCUSSIVE_ATONAL
        )

    if from_tags:
        confidence = 0.85
    elif from_audio:
        confidence = 0.7
    else:
        confidence = 0.4

    energy = calculate_energy(
        bpm,
        lufs=None,
        lra=lra,
        subgenre=matched,
        spectral_centroid_hz=spectral_centroid_hz,
        rms=rms,
    )

    return StyleAnalysis(
        cluster=cluster,
        subgenre=matched,
        flow=flow,
        raster=raster,
        aesthetic=aesthetic,
        energy=energy,
        dj_note=_DJ_NOTES[matched],
        confidence=confidence,
        version_type=detect_version_type(title, tags),
    )


# Anzeigenamen für Subgenre und Fluss (deutschsprachige Rekordbox-Kommentare).
_SUBGENRE_DISPLAY: dict[Subgenre, str] = {
    Subgenre.TRIBAL: "Tribal",
    Subgenre.HARDGROOVE: "Hardgroove",
    Subgenre.CLASSIC: "Classic",
    Subgenre.INDUSTRIAL: "Industrial",
    Subgenre.ACID: "Acid",
    Subgenre.TERROR: "Terror",
    Subgenre.NEO: "Neo",
    Subgenre.MELODIC: "Melodic",
    Subgenre.BOUNCE: "Bounce",
    Subgenre.PSY: "Psy",
    Subgenre.REVERSE_BASS: "Reverse Bass",
    Subgenre.EBM: "EBM",
    Subgenre.CROSSBREED: "Crossbreed",
}
_FLOW_DISPLAY: dict[FlowAxis, str] = {
    FlowAxis.HORIZONTAL: "Horizontal",
    FlowAxis.VERTICAL: "Vertikal",
}


def format_rekordbox_comment(
    analysis: StyleAnalysis,
    bpm: float | None = None,
    key_camelot: str | None = None,
    *,
    include_flow: bool = True,
) -> str:
    """Erzeugt einen Rekordbox-kompatiblen Kommentar-String.
    Beispiel ORIGINAL: '[Bounce] 154 BPM · 5A | Vertikal · ⚠️ Subbass-Phasengefahr!'
    Beispiel VERSION: '[TOOL] 150 BPM · 5A | 3-Deck Loop Tool'"""
    if analysis.version_type in (VersionType.ORIGINAL, VersionType.REMIX):
        label = _SUBGENRE_DISPLAY[analysis.subgenre]
    else:
        label = analysis.version_type.value.upper()

    head = f"[{label}]"
    meta: list[str] = []
    if bpm is not None:
        meta.append(f"{bpm:g} BPM")
    if key_camelot:
        meta.append(key_camelot)
    if meta:
        head += " " + " · ".join(meta)

    tail: list[str] = []
    if include_flow:
        tail.append(_FLOW_DISPLAY[analysis.flow])
    if analysis.dj_note:
        tail.append(analysis.dj_note)

    return f"{head} | " + " · ".join(tail)


def _format_mmss(seconds: float) -> str:
    """Formatiert Sekunden als M:SS (Minuten ohne führende Null, Sekunden zweistellig)."""
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def format_compact_rekordbox_comment(
    analysis: StyleAnalysis,
    bpm: float | None = None,
    key_camelot: str | None = None,
    *,
    drop1_s: float | None = None,
    drop2_s: float | None = None,
    lufs: float | None = None,
) -> str:
    """Erzeugt einen kompakten Rekordbox-Kommentar für kleine CDJ/XDJ-Displays.

    Aufbau: '[<BPM>|<Key>|<Substil>|D1:<Drop1>|D2:<Drop2>|L<LUFS>]'. Nicht gesetzte
    Felder entfallen, die Reihenfolge bleibt fest. Substil ist der Subgenre-Anzeigename,
    bei Versionstypen außer ORIGINAL/REMIX das Versionskürzel (z. B. TOOL). Ohne jedes
    Feld (kein BPM, Key, Drop, LUFS) wird nur '[<Substil>]' zurückgegeben.
    Beispiel: '[155|9A|Hardgroove|D1:0:45|D2:2:15|L-7.5]'."""
    if analysis.version_type in (VersionType.ORIGINAL, VersionType.REMIX):
        label = _SUBGENRE_DISPLAY[analysis.subgenre]
    else:
        label = analysis.version_type.value.upper()

    parts: list[str] = []
    if bpm is not None and bpm > 0:
        parts.append(f"{bpm:g}")
    key = key_camelot.strip() if key_camelot else ""
    if key:
        parts.append(key)
    parts.append(label)
    if drop1_s is not None and drop1_s >= 0:
        parts.append(f"D1:{_format_mmss(drop1_s)}")
    if drop2_s is not None and drop2_s >= 0:
        parts.append(f"D2:{_format_mmss(drop2_s)}")
    if lufs is not None:
        parts.append(f"L{lufs:.1f}")

    return f"[{'|'.join(parts)}]"
