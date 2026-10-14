"""Rekordbox-XML-Export: Playlists pro Woche aus der Inbox für Rekordbox generieren."""
from __future__ import annotations

import json
import logging
import math
import os
import re
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from sc_digger import cues, organize, phrase
from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.style import (
    StyleCluster,
    Subgenre,
    VersionType,
    classify_track,
    format_compact_rekordbox_comment,
    format_rekordbox_comment,
    recommend_rekordbox_color,
)

log = logging.getLogger(__name__)

STYLE_CLUSTER_PLAYLISTS: list[tuple[StyleCluster, str]] = [
    (StyleCluster.FUNDAMENT, "Fundament & Tools"),
    (StyleCluster.NOISE, "Noise & Extremhärte"),
    (StyleCluster.EMOTION, "Emotion & Dramaturgie"),
    (StyleCluster.CROSSOVER, "Rhythmus-Crossover"),
]

SUBGENRE_PLAYLISTS: list[tuple[Subgenre, str]] = [
    (Subgenre.TRIBAL, "Tribal"),
    (Subgenre.HARDGROOVE, "Hardgroove"),
    (Subgenre.CLASSIC, "Classic"),
    (Subgenre.INDUSTRIAL, "Industrial"),
    (Subgenre.ACID, "Acid"),
    (Subgenre.TERROR, "Terror"),
    (Subgenre.NEO, "Neo"),
    (Subgenre.MELODIC, "Melodic"),
    (Subgenre.BOUNCE, "Bounce"),
    (Subgenre.PSY, "Psy"),
    (Subgenre.REVERSE_BASS, "Reverse Bass"),
    (Subgenre.EBM, "EBM"),
    (Subgenre.CROSSBREED, "Crossbreed"),
]

VERSION_TYPE_PLAYLISTS: list[tuple[VersionType, str]] = [
    (VersionType.TOOL, "Tools"),
    (VersionType.EDIT, "Edits"),
    (VersionType.BOOTLEG, "Bootlegs"),
    (VersionType.REMIX, "Remixe"),
    (VersionType.VIP, "VIPs"),
    (VersionType.MASHUP, "Mashups"),
    (VersionType.FLIP, "Flips"),
    (VersionType.REWORK, "Reworks"),
    (VersionType.ORIGINAL, "Originale"),
]

KINDS = {
    ".wav": "WAV File",
    ".aiff": "AIFF File",
    ".aif": "AIFF File",
    ".flac": "FLAC File",
    ".mp3": "MP3 File",
    ".m4a": "M4A File",
}

_SKIP_PREFIXES = ("_rejected", ".", "_dl_", ".cloud-tmp", "_eingang")


@dataclass
class InboxTrack:
    path: Path
    title: str
    artist: str
    genre: str
    bpm: float | None
    key: str
    comment: str
    duration_s: float
    size: int
    added: datetime  # added = mtime (lokale Zeit)
    first_kick_s: float | None = None
    drop_s: float | None = None
    breakdown_s: float | None = None
    breakdown_kind: str | None = None
    drop2_s: float | None = None
    lufs: float | None = None


_SCID_RE = re.compile(r"^\[(\d+)\]")
_SCID_SUFFIX_RE = re.compile(r"_\d+$")


def soundcloud_id_from_name(name: str) -> str | None:
    """ID aus einem Dateinamen, der mit "[<Ziffern>]" beginnt, sonst None. Eine "_<n>"-Endung
    vor der Dateiendung ändert nichts."""
    m = _SCID_RE.match(name)
    if m is None:
        return None
    return m.group(1)


def _dedupe_sort_key(t: InboxTrack) -> tuple[int, int, int, str]:
    """Sortierschlüssel für dedupe_by_soundcloud_id (kleiner = bevorzugt):
    0. getaggt (t.key.strip() nicht leer ODER t.bpm is not None) -> 0, sonst 1,
    1. Stem ohne "_<Ziffern>"-Endung (Original vor Kopie) -> 0, sonst 1,
    2. tieferer Pfad zuerst -> negative Anzahl der Pfadteile,
    3. kleinster path.as_posix()."""
    is_untagged = 0 if (t.key.strip() or t.bpm is not None) else 1
    stem = t.path.stem
    is_copy = 1 if _SCID_SUFFIX_RE.search(stem) else 0
    depth = -len(t.path.parts)
    return (is_untagged, is_copy, depth, t.path.as_posix())


def dedupe_by_soundcloud_id(tracks: list[InboxTrack]) -> list[InboxTrack]:
    """Behält pro SoundCloud-ID (aus path.name) genau einen Track. Gewinner, in dieser Reihenfolge:
    1. getaggt (Key oder BPM vorhanden) vor ungetaggt,
    2. Stem ohne Endung "_<Ziffern>" (Original vor Kopie),
    3. tieferer Pfad (sortierte Datei <BPM>/<Key>/ vor loser Datei im Inbox-Root),
    4. kleinster path.as_posix().
    Tracks ohne ID bleiben unverändert. Die Reihenfolge der übrigen Tracks bleibt erhalten.
    Leere Liste -> leere Liste. Dateien werden nicht gelesen, verschoben oder gelöscht."""
    winners: dict[str, InboxTrack] = {}
    for t in tracks:
        sc_id = soundcloud_id_from_name(t.path.name)
        if sc_id is None:
            continue
        current = winners.get(sc_id)
        if current is None or _dedupe_sort_key(t) < _dedupe_sort_key(current):
            winners[sc_id] = t

    out: list[InboxTrack] = []
    for t in tracks:
        sc_id = soundcloud_id_from_name(t.path.name)
        if sc_id is None:
            out.append(t)
        elif winners[sc_id] is t:
            out.append(t)
    return out


def laptop_location(server_path: str | Path, path_map: dict[str, str]) -> str | None:
    """Rekordbox-Location: "file://localhost/" + quote(<Laptop-Pfad>, safe="/:").

    Pfade vorher mit "/" statt "\\"; Präfix passt nur an Pfadgrenzen (/music/inbox passt nicht
    auf /music/inbox2); bei mehreren passenden Präfixen gewinnt das längste; kein Treffer -> None.
    """
    s = str(server_path).replace("\\", "/")
    best_key: str | None = None
    best_len = -1

    for prefix in path_map:
        p = prefix.replace("\\", "/")
        if p == "/":
            matches = s.startswith("/")
            match_len = 1
        else:
            p_clean = p.rstrip("/")
            matches = s == p_clean or s.startswith(p_clean + "/")
            match_len = len(p_clean)

        if matches and match_len > best_len:
            best_len = match_len
            best_key = prefix

    if best_key is None:
        return None

    target = path_map[best_key].replace("\\", "/").rstrip("/")
    p = best_key.replace("\\", "/")
    if p == "/":
        rel = s
    else:
        p_clean = p.rstrip("/")
        rel = s[len(p_clean):]

    laptop_path = target + rel
    # NFC (zusammengesetzte Umlaute): Rekordbox findet die Datei sonst nicht wieder,
    # wenn SoundCloud den Namen in NFD geliefert hat ("u" + separates Trema).
    laptop_path = unicodedata.normalize("NFC", laptop_path)
    return "file://localhost/" + quote(laptop_path, safe="/:")


def _parse_bpm(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        val = float(str(raw).strip().replace(",", "."))
        return val if val > 0 else None
    except (ValueError, TypeError):
        return None


def _read_track(path: Path) -> InboxTrack:
    """Liest Metadaten einer Audio-Datei via mutagen."""
    stat = path.stat()
    added = datetime.fromtimestamp(stat.st_mtime)
    size = stat.st_size

    try:
        import mutagen

        audio = mutagen.File(str(path))
        if audio is None:
            log.warning("mutagen konnte Datei nicht öffnen: %s", path)
            return InboxTrack(
                path=path,
                title=path.stem,
                artist="",
                genre="",
                bpm=None,
                key="",
                comment="",
                duration_s=0.0,
                size=size,
                added=added,
            )

        duration_s = float(getattr(audio.info, "length", 0.0)) if audio.info else 0.0
        ext = path.suffix.lower()

        title = ""
        artist = ""
        genre = ""
        bpm: float | None = None
        key = ""
        comment = ""

        if ext in (".mp3", ".wav", ".aif", ".aiff"):
            tags = audio.tags
            if tags is not None:
                # TIT2, TPE1, TCON, TBPM, TKEY, erstes COMM
                tit2 = tags.get("TIT2")
                if tit2 and getattr(tit2, "text", None):
                    title = str(tit2.text[0]).strip()
                tpe1 = tags.get("TPE1")
                if tpe1 and getattr(tpe1, "text", None):
                    artist = str(tpe1.text[0]).strip()
                tcon = tags.get("TCON")
                if tcon and getattr(tcon, "text", None):
                    genre = str(tcon.text[0]).strip()
                tbpm = tags.get("TBPM")
                if tbpm and getattr(tbpm, "text", None):
                    bpm = _parse_bpm(tbpm.text[0])
                tkey = tags.get("TKEY")
                if tkey and getattr(tkey, "text", None):
                    key = str(tkey.text[0]).strip()

                if hasattr(tags, "getall"):
                    comms = tags.getall("COMM")
                    if comms and getattr(comms[0], "text", None):
                        comment = str(comms[0].text[0]).strip()
                else:
                    for k, v in tags.items():
                        if k.startswith("COMM") and getattr(v, "text", None):
                            comment = str(v.text[0]).strip()
                            break

        elif ext == ".flac":
            # FLAC title, artist, genre, bpm, initialkey, comment
            def _flac(k: str) -> str:
                vals = audio.get(k)
                if vals:
                    return str(vals[0]).strip()
                return ""

            title = _flac("title")
            artist = _flac("artist")
            genre = _flac("genre")
            bpm = _parse_bpm(_flac("bpm"))
            key = _flac("initialkey")
            comment = _flac("comment")

        elif ext == ".m4a":
            # M4A ©nam, ©ART, ©gen, tmpo, ©cmt
            def _mp4(k: str) -> str:
                vals = audio.get(k)
                if vals:
                    return str(vals[0]).strip()
                return ""

            title = _mp4("\xa9nam")
            artist = _mp4("\xa9ART")
            genre = _mp4("\xa9gen")
            tmpo_vals = audio.get("tmpo")
            if tmpo_vals:
                bpm = _parse_bpm(tmpo_vals[0])
            comment = _mp4("\xa9cmt")

        if not title:
            title = path.stem

        return InboxTrack(
            path=path,
            title=title,
            artist=artist,
            genre=genre,
            bpm=bpm,
            key=key,
            comment=comment,
            duration_s=duration_s,
            size=size,
            added=added,
        )

    except Exception as e:
        log.warning("Nicht lesbare Datei %s: %s", path, e)
        return InboxTrack(
            path=path,
            title=path.stem,
            artist="",
            genre="",
            bpm=None,
            key="",
            comment="",
            duration_s=0.0,
            size=size,
            added=added,
        )


def scan_inbox(inbox: Path) -> list[InboxTrack]:
    """Rekursiv alle Dateien mit Endung aus KINDS, sortiert nach relativem Pfad (as_posix).

    Übersprungen wird alles, bei dem IRGENDEIN Pfadteil unterhalb der Inbox mit "_rejected", ".",
    "_dl_" oder ".cloud-tmp" beginnt (Abgelehntes, Temp-Dateien und -Ordner).
    Tags per mutagen: ID3 (MP3/WAV/AIFF) TIT2, TPE1, TCON, TBPM, TKEY, erstes COMM;
    FLAC title, artist, genre, bpm, initialkey, comment; M4A ©nam, ©ART, ©gen, tmpo, ©cmt.
    Dazu die Cue-Tags aus cues.py: SCDIGGER_FIRST_KICK, SCDIGGER_DROP, SCDIGGER_BREAKDOWN
    und SCDIGGER_BREAKDOWN_KIND (fehlend -> None). Ebenso SCDIGGER_DROP2 (zweiter Drop) und
    SCDIGGER_LUFS (integrierte Lautheit, z. B. "-7.5"; fehlend/unlesbar -> None).
    Fehlende Texte -> "", Titel fällt auf den Dateinamen ohne Endung zurück, BPM nicht lesbar -> None.
    Dauer aus mutagen info.length. Nicht lesbare Datei: log.warning, Eintrag mit Dateiname als Titel.
    """
    inbox = Path(inbox)
    if not inbox.exists():
        return []

    valid_paths: list[Path] = []
    for root, dirs, files in os.walk(inbox):
        root_path = Path(root)
        try:
            rel_root = root_path.relative_to(inbox)
        except ValueError:
            continue

        if any(any(part.startswith(pfx) for pfx in _SKIP_PREFIXES) for part in rel_root.parts):
            continue

        # Prune dirs in place
        dirs[:] = [d for d in dirs if not any(d.startswith(pfx) for pfx in _SKIP_PREFIXES)]

        for f in files:
            if any(f.startswith(pfx) for pfx in _SKIP_PREFIXES):
                continue
            file_path = root_path / f
            if file_path.suffix.lower() in KINDS:
                valid_paths.append(file_path)

    valid_paths.sort(key=lambda p: p.relative_to(inbox).as_posix())
    tracks = [_read_track(p) for p in valid_paths]
    for t in tracks:
        t.first_kick_s = cues.read_first_kick_tag(t.path)
        t.drop_s = cues.read_drop_tag(t.path)
        t.breakdown_s = cues.read_breakdown_tag(t.path)
        t.breakdown_kind = cues.read_breakdown_kind_tag(t.path)
        t.drop2_s = cues.read_drop2_tag(t.path)
        # Für SCDIGGER_LUFS gibt es keinen öffentlichen Leser in cues (negativer Wertebereich);
        # den privaten Helfer dort direkt zu nutzen ist (wie organize._bpm_bucket hier) der Hausstil.
        t.lufs = _parse_lufs(cues._read_string_tag(t.path, "SCDIGGER_LUFS"))
    return tracks


def _parse_lufs(raw: str | None) -> float | None:
    """Parst einen LUFS-Wert aus einem Tag. Anders als cues._parse_seconds sind negative
    Werte gültig (z. B. "-7.5"); fehlend, unlesbar oder nicht-finit -> None."""
    if raw is None:
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


_CAMELOT_RE = re.compile(r"^(?:[1-9]|1[0-2])[AB]$")


def _camelot_sort_key(name: str) -> tuple[int, str]:
    """Sortierschlüssel für Camelot-Playlist-Namen: Zahl, dann Buchstabe; "unbekannt" zuletzt."""
    if name == "unbekannt":
        return (999, name)
    return (int(name[:-1]), name[-1])


def _bpm_sort_key(name: str) -> tuple[int, int]:
    """Sortierschlüssel für BPM-Playlist-Namen: untere Grenze als Zahl; "unbekannt" zuletzt."""
    if name == "unbekannt":
        return (1, 0)
    return (0, int(name.split("-", 1)[0]))


def _playlist_folder(parent: ET.Element, name: str,
                      groups: list[tuple[str, list[str]]]) -> None:
    """Ordner (Type="0") mit Playlists (Type="1" KeyType="0") an parent anhängen.

    groups ist eine Liste (Playlist-Name, Liste von TrackIDs) in gewünschter Reihenfolge.
    """
    folder_node = ET.SubElement(parent, "NODE", {
        "Type": "0",
        "Name": name,
        "Count": str(len(groups)),
    })
    for pl_name, t_ids in groups:
        pl_node = ET.SubElement(folder_node, "NODE", {
            "Name": pl_name,
            "Type": "1",
            "KeyType": "0",
            "Entries": str(len(t_ids)),
        })
        for tid in t_ids:
            ET.SubElement(pl_node, "TRACK", {"Key": tid})


def build_xml(tracks: list[InboxTrack], path_map: dict[str, str], now: datetime | None = None,
              *, bpm_bucket_size: int | None = None, by_key: bool = False,
              hot_cues: bool = True, memory_cues: bool = True,
              by_style: bool = False, by_subgenre: bool = False, by_version: bool = False,
              style_colors: bool = False, style_comments: bool = False,
              phrase_align_cues: bool = False, grid_anchor: bool = True,
              matrix_colors: bool = False, compact_style_comments: bool = False,
              active_loop: bool = False, phrase_warning: bool = False,
              active_loop_beats: int = 16) -> str:
    """<?xml version="1.0" encoding="UTF-8"?> + DJ_PLAYLISTS Version="1.0.0" mit PRODUCT,
    COLLECTION und PLAYLISTS. Tracks ohne Location (nicht gemappt) werden weggelassen.
    COLLECTION Entries = Anzahl; TRACK-Attribute: TrackID (1, 2, … in Eingabereihenfolge), Name,
    Artist, Genre, Kind, Size, TotalTime (ganze Sekunden, gerundet), DateAdded (YYYY-MM-DD),
    Comments, Tonality, Location, AverageBpm (f"{bpm:.2f}", nur wenn bpm nicht None).
    PLAYLISTS: NODE Type="0" Name="ROOT" Count="1" > NODE Type="0" Name="sc-digger" Count=<Kinder>
    > je Woche NODE Name="KW {Woche:02d}/{Jahr}" Type="1" KeyType="0" Entries=<n> mit
    ``<TRACK Key="<TrackID>"/>`` in Eingabereihenfolge. Woche/Jahr = ISO-Kalenderwoche von added,
    neueste Woche zuerst. Alle Attributwerte korrekt XML-maskiert. now: optional, derzeit ungenutzt.

    Bisheriges Verhalten unverändert, solange bpm_bucket_size None/0 und by_key False ist.
    Zusätzlich, nach den Wochen: Ordner "BPM" (falls bpm_bucket_size gesetzt) und "Key" (falls
    by_key). Nur gemappte Tracks kommen vor; ohne Tracks wird der jeweilige Ordner weggelassen.

    Cues aus first_kick_s/drop_s/breakdown_s: hot_cues=True schreibt je Punkt einen Hot Cue
    (First Kick = Pad A Num="0" grün, Drop = Pad B Num="1" orange, Breakdown = Pad D Num="3"
    gelb); memory_cues=True schreibt zusätzlich je Punkt einen Memory Cue (Num="-1") mit
    gleichem Namen. Die Marken stehen chronologisch nach Start, bei gleichem Zeitstempel zuerst
    der Hot Cue. Fehlende Cue-Zeitpunkte entfallen; breakdown_kind "groove" ergibt den Namen
    "Break (Groove)", sonst "Break (Silence)". phrase_align_cues=True richtet Drop und Breakdown
    vor dem Erzeugen der POSITION_MARK-Elemente per phrase.quantize_to_phrase am 16/32-Beat-
    Phrasengitter relativ zu first_kick_s aus (nur wenn first_kick_s und bpm vorhanden sind;
    erster Kick und Standardwert False bleiben unverändert).

    Schranz-Matrix (sc_digger.style), optional: style_colors=True setzt am TRACK-Knoten das
    Colour-Attribut aus recommend_rekordbox_color (ausser "None"); style_comments=True
    überschreibt Comments mit format_rekordbox_comment und ergänzt bei vorliegendem
    breakdown_kind " · Break: <Silence|Groove>". matrix_colors=True setzt die Colour nach
    dem Subgenre (recommend_rekordbox_color(..., by_matrix=True)) und hat Vorrang vor
    style_colors. compact_style_comments=True überschreibt Comments mit
    format_compact_rekordbox_comment (kompakt für CDJ/XDJ-Displays, ohne Break-Zusatz) und
    hat Vorrang vor style_comments. by_style/by_subgenre/by_version
    ergänzen nach den Wochen und BPM/Key die Ordner "Stile", "Subgenres" und "Versionen"
    mit je einer Playlist pro nicht leerem Cluster/Subgenre/Versionstyp in fester
    Reihenfolge; leere Ordner entfallen.

    grid_anchor=True schreibt in jeden TRACK mit first_kick_s >= 0 und bpm > 0 einen
    Beatgrid-Anker <TEMPO Beginning="{first_kick_s:.3f}" Bpm="{bpm:.2f}" Metro="4/4"
    Battito="1"/> vor den POSITION_MARK-Kindern: Rekordbox setzt Takt 1/Beat 1 damit exakt
    auf die Kick-Transient, statt bei 150+ BPM das Raster um ein 16tel zu versetzen.
    grid_anchor=False oder fehlender Kick/BPM: kein TEMPO-Knoten.

    Strategische Memory Cues: Ist drop2_s vorhanden, wird der zweite Drop als Hot Cue
    (Num="2", Pad C) und/oder Memory Cue (Num="-1") mit Namen "Drop 2" exportiert.
    phrase_warning=True (und memory_cues=True) setzt für jedes vorhandene drop_s, breakdown_s
    und drop2_s, das laut phrase.is_off_phrase() außerhalb des 16/32-Beat-Phrasengitters liegt,
    einen roten Warn-Memory-Cue <POSITION_MARK Name="⚠️ Off-Phrase" Type="0" Num="-1"
    Red="255" Green="0" Blue="0"/>. active_loop=True fügt bei bpm > 0 und
    duration_s > active_loop_beats * (60/bpm) vor Track-Ende einen aktiven Memory Loop
    <POSITION_MARK Name="Active Loop" Type="4" Start="…" End="…" Num="-1" Active="1"/>
    der Länge active_loop_beats * (60/bpm) hinzu; der Start wird an die 4-Beat-Taktgrenze
    relativ zu first_kick_s ausgerichtet.
    """
    mapped: list[tuple[str, InboxTrack, str]] = []
    for t in dedupe_by_soundcloud_id(tracks):
        loc = laptop_location(t.path, path_map)
        if loc is not None:
            track_id = str(len(mapped) + 1)
            mapped.append((track_id, t, loc))

    root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
    ET.SubElement(root, "PRODUCT", {"Name": "rekordbox", "Version": "6.0.0", "Company": "Pioneer DJ"})

    coll = ET.SubElement(root, "COLLECTION", {"Entries": str(len(mapped))})
    for track_id, t, loc in mapped:
        attr = {
            "TrackID": track_id,
            "Name": t.title,
            "Artist": t.artist,
            "Genre": t.genre,
            "Kind": KINDS.get(t.path.suffix.lower(), ""),
            "Size": str(t.size),
            "TotalTime": str(int(round(t.duration_s))),
            "DateAdded": t.added.strftime("%Y-%m-%d"),
            "Comments": t.comment,
            "Tonality": t.key,
            "Location": loc,
        }
        if t.bpm is not None:
            attr["AverageBpm"] = f"{t.bpm:.2f}"
        if style_colors or style_comments or matrix_colors or compact_style_comments:
            analysis = classify_track(
                bpm=t.bpm,
                title=t.title,
                tags=[t.genre] if t.genre else None,
            )
            if matrix_colors:
                color = recommend_rekordbox_color(analysis, by_matrix=True)
            elif style_colors:
                color = recommend_rekordbox_color(analysis)
            else:
                color = "None"
            if color != "None":
                attr["Colour"] = color
            if compact_style_comments:
                attr["Comments"] = format_compact_rekordbox_comment(
                    analysis,
                    bpm=t.bpm,
                    key_camelot=t.key or None,
                    drop1_s=t.drop_s,
                    drop2_s=t.drop2_s,
                    lufs=t.lufs,
                )
            elif style_comments:
                attr["Comments"] = format_rekordbox_comment(
                    analysis, bpm=t.bpm, key_camelot=t.key or None
                )
                if t.breakdown_kind:
                    kind = t.breakdown_kind.strip().lower()
                    kind_label = "Groove" if kind == "groove" else "Silence"
                    attr["Comments"] = f"{attr['Comments']} · Break: {kind_label}"
        track_node = ET.SubElement(coll, "TRACK", attr)
        # Beatgrid-Anker vor den Cue-Marken: Rekordbox verankert Takt 1/Beat 1 damit exakt
        # auf der Kick-Transient (bei 150+ BPM versetzt die Transienten-Erkennung das Raster).
        if grid_anchor and t.first_kick_s is not None and t.bpm is not None:
            kick_s = float(t.first_kick_s)
            anchor_bpm = float(t.bpm)
            if kick_s >= 0 and anchor_bpm > 0:
                ET.SubElement(track_node, "TEMPO", {
                    "Beginning": f"{kick_s:.3f}",
                    "Bpm": f"{anchor_bpm:.2f}",
                    "Metro": "4/4",
                    "Battito": "1",
                })
        # Ein Cue-Punkt je vorhandener Zeit: (Start, Hot-Cue-Pad, Name, Farbe).
        drop_s = t.drop_s
        breakdown_s = t.breakdown_s
        drop2_s = t.drop2_s
        if phrase_align_cues and t.first_kick_s is not None and t.bpm is not None:
            kick = float(t.first_kick_s)
            bpm = float(t.bpm)
            if drop_s is not None:
                drop_s = phrase.quantize_to_phrase(float(drop_s), kick, bpm)
            if breakdown_s is not None:
                breakdown_s = phrase.quantize_to_phrase(float(breakdown_s), kick, bpm)

        cue_points: list[tuple[float, str, str, tuple[str, str, str]]] = []
        if t.first_kick_s is not None:
            cue_points.append((float(t.first_kick_s), "0", "First Kick", ("0", "255", "0")))
        if drop_s is not None:
            cue_points.append((float(drop_s), "1", "Drop", ("255", "165", "0")))
        if drop2_s is not None:
            # Zweiter Drop = Pad C (Num="2"); Rekordbox-Notlauftaste, rot markiert.
            cue_points.append((float(drop2_s), "2", "Drop 2", ("255", "0", "0")))
        if breakdown_s is not None:
            kind = (t.breakdown_kind or "").strip().lower()
            break_name = "Break (Groove)" if kind == "groove" else "Break (Silence)"
            cue_points.append((float(breakdown_s), "3", break_name, ("255", "255", "0")))

        # Chronologisch nach Start; bei gleichem Zeitstempel steht der Hot Cue vor dem Memory Cue.
        marks: list[tuple[float, int, str, str, tuple[str, str, str] | None]] = []
        for start_s, pad, name, color in cue_points:
            if hot_cues:
                marks.append((start_s, 0, name, pad, color))
            if memory_cues:
                marks.append((start_s, 1, name, "-1", None))

        # Warn-Memory-Cues an Strukturbrüchen außerhalb des 16/32-Beat-Phrasengitters.
        if phrase_warning and memory_cues and t.first_kick_s is not None and t.bpm is not None:
            kick = float(t.first_kick_s)
            bpm = float(t.bpm)
            if bpm > 0 and kick >= 0:
                for event_s in (drop_s, breakdown_s, drop2_s):
                    if event_s is None:
                        continue
                    if phrase.is_off_phrase(float(event_s), kick, bpm):
                        marks.append((float(event_s), 1, "⚠️ Off-Phrase", "-1",
                                      ("255", "0", "0")))

        marks.sort(key=lambda m: (m[0], m[1]))

        for start_s, _is_memory, name, num, color in marks:
            mark_attr = {"Name": name, "Type": "0", "Start": f"{start_s:.3f}", "Num": num}
            if color is not None:
                mark_attr["Red"], mark_attr["Green"], mark_attr["Blue"] = color
            ET.SubElement(track_node, "POSITION_MARK", mark_attr)

        # Notbremse: aktiver Memory Loop am Track-Ende, an der 4-Beat-Taktgrenze ausgerichtet.
        if active_loop and t.bpm is not None and t.duration_s:
            loop_bpm = float(t.bpm)
            loop_beats = int(active_loop_beats)
            duration_s = float(t.duration_s)
            if loop_bpm > 0 and loop_beats > 0:
                beat_s = 60.0 / loop_bpm
                loop_len = loop_beats * beat_s
                if duration_s > loop_len:
                    kick = float(t.first_kick_s) if t.first_kick_s is not None else 0.0
                    raw_start = duration_s - loop_len
                    bar_index = math.floor((raw_start - kick) / (4.0 * beat_s))
                    start_s = kick + bar_index * 4.0 * beat_s
                    end_s = start_s + loop_len
                    ET.SubElement(track_node, "POSITION_MARK", {
                        "Name": "Active Loop",
                        "Type": "4",
                        "Start": f"{start_s:.3f}",
                        "End": f"{end_s:.3f}",
                        "Num": "-1",
                        "Active": "1",
                    })

    playlists = ET.SubElement(root, "PLAYLISTS")
    top = ET.SubElement(playlists, "NODE", {"Type": "0", "Name": "ROOT", "Count": "1"})

    weeks_dict: dict[tuple[int, int], list[str]] = {}
    for track_id, t, _ in mapped:
        iso = t.added.isocalendar()
        week_key = (iso[0], iso[1])
        weeks_dict.setdefault(week_key, []).append(track_id)

    sorted_weeks = sorted(weeks_dict.keys(), reverse=True)
    folder = ET.SubElement(top, "NODE", {
        "Type": "0",
        "Name": "sc-digger",
        "Count": str(len(sorted_weeks)),
    })

    for year, week in sorted_weeks:
        t_ids = weeks_dict[(year, week)]
        week_node = ET.SubElement(folder, "NODE", {
            "Name": f"KW {week:02d}/{year}",
            "Type": "1",
            "KeyType": "0",
            "Entries": str(len(t_ids)),
        })
        for tid in t_ids:
            ET.SubElement(week_node, "TRACK", {"Key": tid})

    extra_folders = 0

    if bpm_bucket_size:
        bpm_dict: dict[str, list[str]] = {}
        for track_id, t, _ in mapped:
            label = organize._bpm_bucket(t.bpm, bpm_bucket_size)
            if label == organize._bpm_bucket(None, bpm_bucket_size):
                label = "unbekannt"
            bpm_dict.setdefault(label, []).append(track_id)
        if bpm_dict:
            groups = sorted(bpm_dict.items(), key=lambda kv: _bpm_sort_key(kv[0]))
            _playlist_folder(folder, "BPM", groups)
            extra_folders += 1

    if by_key:
        key_dict: dict[str, list[str]] = {}
        for track_id, t, _ in mapped:
            name = t.key.strip().upper()
            label = name if _CAMELOT_RE.match(name) else "unbekannt"
            key_dict.setdefault(label, []).append(track_id)
        if key_dict:
            groups = sorted(key_dict.items(), key=lambda kv: _camelot_sort_key(kv[0]))
            _playlist_folder(folder, "Key", groups)
            extra_folders += 1

    if by_style or by_subgenre or by_version:
        analyses: dict[str, Any] = {}
        for track_id, t, _ in mapped:
            analyses[track_id] = classify_track(
                bpm=t.bpm,
                title=t.title,
                tags=[t.genre] if t.genre else None,
            )

    if by_style:
        cluster_dict: dict[StyleCluster, list[str]] = {}
        for track_id, t, _ in mapped:
            cluster_dict.setdefault(analyses[track_id].cluster, []).append(track_id)
        groups = [(name, cluster_dict[cluster]) for cluster, name in STYLE_CLUSTER_PLAYLISTS
                  if cluster_dict.get(cluster)]
        if groups:
            _playlist_folder(folder, "Stile", groups)
            extra_folders += 1

    if by_subgenre:
        subgenre_dict: dict[Subgenre, list[str]] = {}
        for track_id, t, _ in mapped:
            subgenre_dict.setdefault(analyses[track_id].subgenre, []).append(track_id)
        groups = [(name, subgenre_dict[subgenre]) for subgenre, name in SUBGENRE_PLAYLISTS
                  if subgenre_dict.get(subgenre)]
        if groups:
            _playlist_folder(folder, "Subgenres", groups)
            extra_folders += 1

    if by_version:
        version_dict: dict[VersionType, list[str]] = {}
        for track_id, t, _ in mapped:
            version_dict.setdefault(analyses[track_id].version_type, []).append(track_id)
        groups = [(name, version_dict[vt]) for vt, name in VERSION_TYPE_PLAYLISTS
                  if version_dict.get(vt)]
        if groups:
            _playlist_folder(folder, "Versionen", groups)
            extra_folders += 1

    folder.set("Count", str(len(sorted_weeks) + extra_folders))

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def write_rekordbox_xml(cfg: Config) -> Path | None:
    """Liest cfg["rekordbox"] (xml_enabled, xml_path, path_map, playlists_by_bpm, playlists_by_key)
    und download.inbox_dir.

    xml_enabled falsch/fehlt -> None. xml_path innerhalb von download.collection_dir -> log.warning,
    None, nichts geschrieben (Goldene Regel 4). Sonst scan_inbox + build_xml und atomar schreiben:
    Temp-Datei im Zielordner (Name beginnt mit ".") + os.replace, UTF-8. Rückgabe: Zielpfad.
    playlists_by_bpm (bool, fehlt -> False): build_xml mit bpm_bucket_size = organize.bpm_bucket_size;
    ist der Wert kein int >= 1 (oder fehlt der Abschnitt), gilt 5. playlists_by_key (bool, fehlt ->
    False): by_key=True. cues_as_hotcues (bool, fehlt -> True): Hot Cues schreiben; cues_as_memory
    (bool, fehlt -> True): Memory Cues schreiben. cues_enabled False -> gar keine Cue-Marken
    (drop_s, breakdown_s und breakdown_kind werden verworfen); first_kick_s bleibt erhalten,
    weil der Beatgrid-Anker (grid_anchor) unabhängig von den Cue-Marken gesetzt wird.
    cues_phrase_align (bool, fehlt -> False): Drop und Breakdown vor dem Export auf das
    16/32-Beat-Phrasengitter ausrichten (build_xml phrase_align_cues).
    grid_anchor (bool, fehlt/None -> True): Beatgrid-Anker <TEMPO> an first_kick_s schreiben
    (build_xml grid_anchor).
    style_colors/style_comments (bool, fehlt -> False): Track-Farbe bzw. -Kommentar nach
    Versionstyp (build_xml). matrix_colors (bool, fehlt -> False): Track-Farbe nach
    Schranz-Matrix (Vorrang vor style_colors). compact_style_comments (bool, fehlt -> False):
    kompakter Display-Kommentar (Vorrang vor style_comments).
    phrase_warning (bool, fehlt -> False): rote Warn-Memory-Cues "⚠️ Off-Phrase" an
    Off-Phrase-Drops/Breakdowns (build_xml). active_loop (bool, fehlt -> False): aktiver
    Memory Loop am Track-Ende (Notbremse). active_loop_beats (int >= 1, fehlt/ungültig -> 16):
    Länge des Notbremsen-Loops in Beats.
    Jeder Fehler -> log.warning, Temp-Datei entfernen, None. Wirft nie.
    """
    tmp_path: Path | None = None
    try:
        try:
            rb_cfg = cfg["rekordbox"]
        except (KeyError, TypeError):
            return None

        if not rb_cfg or not isinstance(rb_cfg, dict):
            return None
        if not rb_cfg.get("xml_enabled"):
            return None

        xml_path_str = rb_cfg.get("xml_path")
        if not xml_path_str:
            return None
        xml_path = Path(xml_path_str)

        try:
            dl_cfg = cfg["download"]
        except (KeyError, TypeError):
            dl_cfg = {}

        coll_dir_str = dl_cfg.get("collection_dir") if isinstance(dl_cfg, dict) else None
        if coll_dir_str:
            coll_dir = Path(coll_dir_str).resolve()
            try:
                target_resolved = xml_path.resolve()
                if target_resolved == coll_dir or target_resolved.is_relative_to(coll_dir):
                    log.warning(
                        "XML-Pfad %s liegt in der geschützten Sammlung %s – abgebrochen (Goldene Regel 4)",
                        xml_path,
                        coll_dir,
                    )
                    return None
            except Exception:
                pass

        inbox_dir_str = dl_cfg.get("inbox_dir") if isinstance(dl_cfg, dict) else None
        if not inbox_dir_str:
            log.warning("Kein inbox_dir in config konfiguriert")
            return None
        inbox_dir = Path(inbox_dir_str)

        path_map = rb_cfg.get("path_map", {})
        tracks = scan_inbox(inbox_dir)

        if not rb_cfg.get("cues_enabled"):
            # Kein Cue-Export: Drop/Breakdown verwerfen. first_kick_s bleibt erhalten, weil der
            # Beatgrid-Anker (grid_anchor) unabhängig von den Cue-Marken gesetzt wird.
            for t in tracks:
                t.drop_s = None
                t.breakdown_s = None
                t.breakdown_kind = None

        bpm_bucket_size: int | None = None
        if rb_cfg.get("playlists_by_bpm"):
            raw_size: Any = None
            try:
                raw_size = cfg["organize"].get("bpm_bucket_size")
            except (KeyError, TypeError, AttributeError):
                raw_size = None
            if isinstance(raw_size, bool) or not isinstance(raw_size, int) or raw_size < 1:
                raw_size = organize._BPM_BUCKET_SIZE
            bpm_bucket_size = raw_size

        by_key = bool(rb_cfg.get("playlists_by_key"))

        by_style = bool(rb_cfg.get("playlists_by_style"))
        by_subgenre = bool(rb_cfg.get("playlists_by_subgenre"))
        by_version = bool(rb_cfg.get("playlists_by_version"))
        style_colors = bool(rb_cfg.get("style_colors"))
        style_comments = bool(rb_cfg.get("style_comments"))
        matrix_colors = bool(rb_cfg.get("matrix_colors"))
        compact_style_comments = bool(rb_cfg.get("compact_style_comments"))

        hot_cues = rb_cfg.get("cues_as_hotcues", True)
        memory_cues = rb_cfg.get("cues_as_memory", True)
        hot_cues = True if hot_cues is None else bool(hot_cues)
        memory_cues = True if memory_cues is None else bool(memory_cues)
        if not rb_cfg.get("cues_enabled"):
            # cues_enabled=False schaltet alle Cue-Marken ab; der Beatgrid-Anker bleibt.
            hot_cues = False
            memory_cues = False
        phrase_align_cues = bool(rb_cfg.get("cues_phrase_align", False))

        grid_anchor = rb_cfg.get("grid_anchor", True)
        grid_anchor = True if grid_anchor is None else bool(grid_anchor)

        phrase_warning = bool(rb_cfg.get("phrase_warning", False))
        active_loop = bool(rb_cfg.get("active_loop", False))
        raw_loop_beats: Any = rb_cfg.get("active_loop_beats", 16)
        if isinstance(raw_loop_beats, bool) or not isinstance(raw_loop_beats, int) \
                or raw_loop_beats < 1:
            raw_loop_beats = 16

        xml_content = build_xml(tracks, path_map, bpm_bucket_size=bpm_bucket_size, by_key=by_key,
                                hot_cues=hot_cues, memory_cues=memory_cues,
                                by_style=by_style, by_subgenre=by_subgenre, by_version=by_version,
                                style_colors=style_colors, style_comments=style_comments,
                                phrase_align_cues=phrase_align_cues, grid_anchor=grid_anchor,
                                matrix_colors=matrix_colors,
                                compact_style_comments=compact_style_comments,
                                active_loop=active_loop, phrase_warning=phrase_warning,
                                active_loop_beats=raw_loop_beats)

        target_dir = xml_path.parent
        target_dir.mkdir(parents=True, exist_ok=True)

        fd, tmp_name = tempfile.mkstemp(dir=target_dir, prefix=".", suffix=".xml.tmp")
        tmp_path = Path(tmp_name)
        with open(fd, "w", encoding="utf-8") as f:
            f.write(xml_content)

        os.replace(tmp_path, xml_path)
        log.info("Rekordbox-XML geschrieben: %s (%d Tracks)", xml_path, len(tracks))
        return xml_path

    except Exception as e:
        log.warning("Fehler beim Erzeugen der Rekordbox-XML: %s", e)
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        return None


# ================================================================= Rekordbox-Import (nur lesend)
# Liest einen Rekordbox-XML-Export (den Rückkanal vom DJ-Laptop) und übernimmt Rating,
# Abspielzähler, BPM, Tonart und Cue-Punkte in die TrackDB. Die Audio-Sammlung wird nie
# berührt (Goldene Regel 4); geschrieben wird ausschließlich in tracks.sqlite.

_RATING_STEP = 51  # Rekordbox speichert Sterne als 0..255 in 51er-Schritten
_AUTO_LIKE_MIN_RATING = 4
_AUTO_LIKE_MIN_PLAYS = 2


@dataclass
class RekordboxTrackData:
    location: str                   # Originale Location-URL (file://localhost/...)
    local_path: str                 # Durch Reverse-Mapping aufgelöster lokaler Pfad
    name: str                       # Track-Name
    artist: str                     # Artist
    rating: int | None              # 0..5 Sterne (0->0, 51->1, 102->2, 153->3, 204->4, 255->5)
    play_count: int | None          # PlayCount als int, None falls fehlt
    bpm: float | None               # AverageBpm als float, None falls fehlt
    key: str | None                 # Tonality als str, None falls fehlt
    cues: list[dict[str, Any]]      # POSITION_MARK-Liste (name, type, start, num, ggf. color)


@dataclass
class RekordboxImportSummary:
    total_tracks: int = 0           # Tracks in der Rekordbox-XML
    matched: int = 0                # In der TrackDB gefundene Tracks
    unmatched: int = 0              # Nicht in der TrackDB gefundene Tracks
    ratings_count: int = 0          # Gematchte Tracks mit Rating
    played_count: int = 0           # Gematchte Tracks mit play_count > 0
    auto_likes_count: int = 0       # Neu durch Auto-Like auf 'like' gesetzte Tracks


def _decode_location(location: str) -> str:
    """Dekodiert eine Rekordbox-Location-URL zu einem Dateipfad.

    Entfernt das Schema ("file://localhost/" oder "file:///"), Prozent-Escapes (z. B. %20),
    wandelt Backslashes in "/" und normalisiert nach NFC (Umlaute wie beim Export).
    """
    s = str(location).strip()
    for prefix in ("file://localhost/", "file:///"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return unicodedata.normalize("NFC", unquote(s).replace("\\", "/"))


def _apply_reverse_path_map(laptop_path: str, path_map: dict[str, str]) -> str:
    """Übersetzt einen Laptop-Pfad über die Umkehrung von path_map (Server-Pfad -> Laptop-Pfad)
    in einen lokalen Server-Pfad. Präfixe passen nur an Pfadgrenzen; das längste gewinnt.
    Ohne Treffer bleibt der Pfad unverändert."""
    s = laptop_path.replace("\\", "/")
    best_len = -1
    best_server: str | None = None
    for server, laptop in path_map.items():
        prefix = str(laptop).replace("\\", "/").rstrip("/")
        if not prefix:
            continue
        if s == prefix or s.startswith(prefix + "/"):
            if len(prefix) > best_len:
                best_len = len(prefix)
                best_server = str(server).replace("\\", "/").rstrip("/")
    if best_server is None:
        return s
    return unicodedata.normalize("NFC", best_server + s[best_len:])


def _parse_rating(raw: Any) -> int | None:
    """Rekordbox-Rating (0..255) auf 0..5 Sterne runden. Fehlt das Attribut oder ist es
    ungültig -> None (nicht 0: "kein Rating" und "Bewertung 0 Sterne" sind verschieden)."""
    if raw is None:
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    stars = (value + _RATING_STEP // 2) // _RATING_STEP
    return max(0, min(5, stars))


def _parse_int_attr(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _parse_float_attr(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(str(raw).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def _parse_position_marks(node: ET.Element) -> list[dict[str, Any]]:
    """Liest die POSITION_MARK-Kinder eines TRACK-Knotens als schlichte Dicts.

    Farbwerte werden nur übernommen, wenn alle drei RGB-Attribute vorhanden sind
    (Memory Cues tragen keine Farbe).
    """
    marks: list[dict[str, Any]] = []
    for mark in node.findall("POSITION_MARK"):
        cue: dict[str, Any] = {
            "name": mark.get("Name") or "",
            "type": _parse_int_attr(mark.get("Type")) or 0,
            "start": _parse_float_attr(mark.get("Start")) or 0.0,
            "num": _parse_int_attr(mark.get("Num")) or 0,
        }
        red = _parse_int_attr(mark.get("Red"))
        green = _parse_int_attr(mark.get("Green"))
        blue = _parse_int_attr(mark.get("Blue"))
        if red is not None and green is not None and blue is not None:
            cue["color"] = {"red": red, "green": green, "blue": blue}
        marks.append(cue)
    return marks


def parse_rekordbox_xml(
    xml_content_or_path: str | Path,
    path_map: dict[str, str] | None = None,
) -> list[RekordboxTrackData]:
    """Parst eine Rekordbox-XML und extrahiert Metadaten aller COLLECTION-Tracks.

    Akzeptiert den XML-Inhalt als String (beginnt nach Leerraum mit "<") oder einen Pfad.
    Dekodiert file://localhost/..., normalisiert Windows/Posix-Pfade und NFC und wendet
    das Reverse-Mapping von path_map an (Laptop-Pfad -> Server-Pfad). Ohne COLLECTION
    oder ohne TRACK-Einträge -> leere Liste. Liest nur, schreibt nie.
    """
    if isinstance(xml_content_or_path, Path):
        content = xml_content_or_path.read_text(encoding="utf-8")
    elif isinstance(xml_content_or_path, str) and not xml_content_or_path.lstrip().startswith("<"):
        content = Path(xml_content_or_path).read_text(encoding="utf-8")
    else:
        content = str(xml_content_or_path)

    root = ET.fromstring(content)
    collection = root.find("COLLECTION")
    if collection is None:
        return []

    mapping = path_map or {}
    tracks: list[RekordboxTrackData] = []
    for node in collection.findall("TRACK"):
        location = node.get("Location") or ""
        local_path = _decode_location(location)
        if mapping:
            local_path = _apply_reverse_path_map(local_path, mapping)
        tracks.append(RekordboxTrackData(
            location=location,
            local_path=local_path,
            name=node.get("Name") or "",
            artist=node.get("Artist") or "",
            rating=_parse_rating(node.get("Rating")),
            play_count=_parse_int_attr(node.get("PlayCount")),
            bpm=_parse_float_attr(node.get("AverageBpm")),
            key=node.get("Tonality") or None,
            cues=_parse_position_marks(node),
        ))
    return tracks


def import_rekordbox_xml(
    db: TrackDB,
    xml_path: str | Path,
    path_map: dict[str, str],
    *,
    auto_like: bool = True,
) -> RekordboxImportSummary:
    """Importiert Metadaten aus einer Rekordbox-XML in die TrackDB.

    Matching-Reihenfolge je Track:
    1. Direkt über db.get_track_by_path(local_path)
    2. Fallback über den Dateinamen: Path(local_path).name via db.get_track_by_filename(...)
    Bei Treffer werden Rating, PlayCount, BPM, Key und Cues gespeichert; auto_like=True
    setzt bei hoher Bewertung (Rating >= 4 oder Plays >= 2) ein noch leeres Feedback auf
    'like' (nie ein bestehendes). Nicht gefundene Tracks zählen als unmatched und werden
    nicht angelegt. Wiederholtes Einlesen derselben XML ist idempotent.
    """
    parsed = parse_rekordbox_xml(xml_path, path_map)
    summary = RekordboxImportSummary(total_tracks=len(parsed))

    for track in parsed:
        record = db.get_track_by_path(track.local_path)
        if record is None:
            record = db.get_track_by_filename(Path(track.local_path).name)
        if record is None or record.id is None:
            summary.unmatched += 1
            continue

        summary.matched += 1
        if track.rating is not None:
            summary.ratings_count += 1
        if track.play_count is not None and track.play_count > 0:
            summary.played_count += 1

        # Dieselbe Schwelle wie update_rekordbox_metadata: zählt nur, was dort neu gesetzt wird.
        will_auto_like = (
            auto_like
            and record.feedback is None
            and (
                (track.rating is not None and track.rating >= _AUTO_LIKE_MIN_RATING)
                or (track.play_count is not None and track.play_count >= _AUTO_LIKE_MIN_PLAYS)
            )
        )

        db.update_rekordbox_metadata(
            record.id,
            rb_rating=track.rating,
            rb_play_count=track.play_count,
            rb_bpm=track.bpm,
            rb_key=track.key,
            rb_cues=json.dumps(track.cues, ensure_ascii=False),
            auto_like=auto_like,
        )
        if will_auto_like:
            summary.auto_likes_count += 1

    return summary

