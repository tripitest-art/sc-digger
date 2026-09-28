"""Rekordbox-XML-Export: Playlists pro Woche aus der Inbox für Rekordbox generieren."""
from __future__ import annotations

import logging
import os
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from typing import Any

from sc_digger.models import Config

log = logging.getLogger(__name__)

KINDS = {
    ".wav": "WAV File",
    ".aiff": "AIFF File",
    ".aif": "AIFF File",
    ".flac": "FLAC File",
    ".mp3": "MP3 File",
    ".m4a": "M4A File",
}

_SKIP_PREFIXES = ("_rejected", ".", "_dl_", ".cloud-tmp")


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
    return [_read_track(p) for p in valid_paths]


def build_xml(tracks: list[InboxTrack], path_map: dict[str, str], now: datetime | None = None) -> str:
    """<?xml version="1.0" encoding="UTF-8"?> + DJ_PLAYLISTS Version="1.0.0" mit PRODUCT,
    COLLECTION und PLAYLISTS. Tracks ohne Location (nicht gemappt) werden weggelassen.
    COLLECTION Entries = Anzahl; TRACK-Attribute: TrackID (1, 2, … in Eingabereihenfolge), Name,
    Artist, Genre, Kind, Size, TotalTime (ganze Sekunden, gerundet), DateAdded (YYYY-MM-DD),
    Comments, Tonality, Location, AverageBpm (f"{bpm:.2f}", nur wenn bpm nicht None).
    PLAYLISTS: NODE Type="0" Name="ROOT" Count="1" > NODE Type="0" Name="sc-digger" Count=<Wochen>
    > je Woche NODE Name="KW {Woche:02d}/{Jahr}" Type="1" KeyType="0" Entries=<n> mit
    ``<TRACK Key="<TrackID>"/>`` in Eingabereihenfolge. Woche/Jahr = ISO-Kalenderwoche von added,
    neueste Woche zuerst. Alle Attributwerte korrekt XML-maskiert. now: optional, derzeit ungenutzt.
    """
    mapped: list[tuple[str, InboxTrack, str]] = []
    for t in tracks:
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
        ET.SubElement(coll, "TRACK", attr)

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

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def write_rekordbox_xml(cfg: Config) -> Path | None:
    """Liest cfg["rekordbox"] (xml_enabled, xml_path, path_map) und download.inbox_dir.

    xml_enabled falsch/fehlt -> None. xml_path innerhalb von download.collection_dir -> log.warning,
    None, nichts geschrieben (Goldene Regel 4). Sonst scan_inbox + build_xml und atomar schreiben:
    Temp-Datei im Zielordner (Name beginnt mit ".") + os.replace, UTF-8. Rückgabe: Zielpfad.
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
        xml_content = build_xml(tracks, path_map)

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
