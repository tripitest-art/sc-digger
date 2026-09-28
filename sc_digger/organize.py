"""Auto-Organize: Sortiert heruntergeladene Tracks nach BPM/Key und schreibt ID3-Tags.

Tracks werden nach dem Schema inbox/<BPM-Range>/<Key>/ einsortiert und mit
vollständigen Metadaten getaggt, sodass sie in DJ-Software sofort nutzbar sind.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import struct
import tempfile
from pathlib import Path
from typing import Any, BinaryIO

log = logging.getLogger(__name__)

# Default-Bereich-Breite für Ordner-Einteilung, falls kein bucket_size übergeben wird.
# config.yaml -> organize.bpm_bucket_size überschreibt das (siehe main.py).
_BPM_BUCKET_SIZE = 5


def _bpm_bucket(bpm: float | None, bucket_size: int = _BPM_BUCKET_SIZE) -> str:
    """Erzeugt ein BPM-Bucket-Label, z.B. '150-155'."""
    if bpm is None:
        return "unknown-bpm"
    lo = int(bpm // bucket_size) * bucket_size
    return f"{lo}-{lo + bucket_size}"


def organize(path: Path, inbox: Path, *, bpm: float | None = None,
             key_camelot: str | None = None, bucket_size: int = _BPM_BUCKET_SIZE) -> Path:
    """Verschiebt einen Track in die passende BPM/Key-Unterordner-Struktur.

    Schema: inbox/<bpm_bucket>/<camelot_key>/<filename>
    z.B.:   inbox/155-160/5A/Artist - Track.wav

    bucket_size kommt aus config.yaml (organize.bpm_bucket_size), Default 5.

    Returns:
        Der neue Dateipfad.
    """
    bpm_dir = _bpm_bucket(bpm, bucket_size)
    key_dir = key_camelot or "unknown-key"
    target_dir = inbox / bpm_dir / key_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    target = target_dir / path.name
    # Namenskollision vermeiden
    if target.exists() and target != path:
        stem = path.stem
        suffix = path.suffix
        n = 1
        while target.exists():
            target = target_dir / f"{stem}_{n}{suffix}"
            n += 1

    if path.parent != target_dir:
        shutil.move(str(path), str(target))
        log.info("Organisiert: %s -> %s", path.name, target.relative_to(inbox))
    return target


REPLAYGAIN_REFERENCE_LUFS = -18.0  # ReplayGain 2.0
_LOUDNESS_TAG_KEYS = (
    "REPLAYGAIN_TRACK_GAIN",
    "REPLAYGAIN_TRACK_PEAK",
    "SCDIGGER_LUFS",
    "SCDIGGER_LRA",
)


def loudness_tag_values(report: dict | None) -> dict[str, str]:
    """Formatierte Lautheits-Tags aus einem quality_report.

    Liest report["integrated_lufs"], report["true_peak_dbfs"], report["loudness_range_lu"]
    (jeweils float oder None, fehlende Schlüssel wie None behandeln).
    Rückgabe, jeder Schlüssel nur, wenn der zugrunde liegende Messwert nicht None ist:
      "REPLAYGAIN_TRACK_GAIN": f"{-18.0 - lufs:.2f} dB"          aus integrated_lufs
      "REPLAYGAIN_TRACK_PEAK": f"{10 ** (true_peak_dbfs / 20):.6f}" (linear)
      "SCDIGGER_LUFS":         f"{lufs:.1f}"                       aus integrated_lufs
      "SCDIGGER_LRA":          f"{loudness_range_lu:.1f}"
    report None oder ohne Messwerte -> {}. Wirft nie.
    """
    if not isinstance(report, dict):
        return {}
    out: dict[str, str] = {}
    try:
        lufs = report.get("integrated_lufs")
        if lufs is not None:
            out["REPLAYGAIN_TRACK_GAIN"] = f"{REPLAYGAIN_REFERENCE_LUFS - float(lufs):.2f} dB"
            out["SCDIGGER_LUFS"] = f"{float(lufs):.1f}"

        tp = report.get("true_peak_dbfs")
        if tp is not None:
            out["REPLAYGAIN_TRACK_PEAK"] = f"{10 ** (float(tp) / 20):.6f}"

        lra = report.get("loudness_range_lu")
        if lra is not None:
            out["SCDIGGER_LRA"] = f"{float(lra):.1f}"
    except Exception as e:
        log.warning("Lautheits-Tags konnten nicht berechnet werden: %s", e)
        return {}
    return out


def write_tags(path: Path, *, artist: str, title: str, bpm: float | None = None,
               key_name: str | None = None, genre: str = "Schranz",
               comment: str = "", url: str = "",
               loudness: dict | None = None) -> bool:
    """Schreibt Metadaten- und Lautheits-Tags in die Audio-Datei.

    Unterstützt: MP3, FLAC, AIFF, M4A, WAV.
    - loudness: quality_report; die Werte aus loudness_tag_values werden geschrieben.
      Vorhandene Felder mit diesen vier Namen werden vorher entfernt (kein Doppel).
    - Ablage: ID3 (MP3, AIFF, WAV) als TXXX-Frames mit desc = Schlüsselname;
      FLAC als Vorbis-Kommentar; M4A als Freeform "----:com.apple.iTunes:<Schlüssel>" (UTF-8).
    - WAV: bekommt jetzt dieselben ID3-Tags wie AIFF (mutagen.wave.WAVE, add_tags bei Bedarf)
      und gibt bei Erfolg True zurück. Kaputte Dateien weiterhin False, keine Exception.
    Gibt True zurück wenn Tags geschrieben wurden.
    """
    try:
        import mutagen
        from mutagen.easyid3 import EasyID3
        from mutagen.id3 import ID3, TBPM, TKEY, COMM, WXXX, TXXX, ID3NoHeaderError
        from mutagen.flac import FLAC
        from mutagen.mp4 import MP4
        from mutagen.aiff import AIFF
        from mutagen.wave import WAVE
    except ImportError:
        log.warning("mutagen nicht installiert – Tagging übersprungen")
        return False

    ext = path.suffix.lower()
    try:
        if ext == ".mp3":
            _tag_mp3(path, artist=artist, title=title, bpm=bpm,
                     key_name=key_name, genre=genre, comment=comment, url=url,
                     loudness=loudness)
        elif ext == ".flac":
            _tag_flac(path, artist=artist, title=title, bpm=bpm,
                      key_name=key_name, genre=genre, comment=comment, url=url,
                      loudness=loudness)
        elif ext in (".aif", ".aiff"):
            _tag_aiff(path, artist=artist, title=title, bpm=bpm,
                      key_name=key_name, genre=genre, comment=comment, url=url,
                      loudness=loudness)
        elif ext == ".m4a":
            _tag_m4a(path, artist=artist, title=title, bpm=bpm,
                     key_name=key_name, genre=genre, comment=comment, url=url,
                     loudness=loudness)
        elif ext == ".wav":
            _tag_wav(path, artist=artist, title=title, bpm=bpm,
                     key_name=key_name, genre=genre, comment=comment, url=url,
                     loudness=loudness)
        else:
            log.debug("Unbekanntes Format für Tagging: %s", ext)
            return False
        log.info("Tags geschrieben: %s", path.name)
        return True
    except Exception as e:
        log.warning("Tagging fehlgeschlagen für %s: %s", path.name, e)
        return False


def _replace_id3_loudness(tags: Any, loudness: object) -> None:
    """Ersetzt bestehende Lautheits-Tags in einem ID3-Objekt durch berechnete TXXX-Frames."""
    from mutagen.id3 import TXXX
    for k in list(tags.keys()):
        if any(k.upper() == f"TXXX:{name.upper()}" for name in _LOUDNESS_TAG_KEYS):
            del tags[k]
    loudness_dict = loudness_tag_values(loudness if isinstance(loudness, dict) else None)
    for name, val in loudness_dict.items():
        tags.add(TXXX(encoding=3, desc=name, text=[val]))


def _fill_id3(tags: Any, kw: dict[str, object]) -> None:
    """Schreibt alle Standard-ID3-Frames und Lautheits-Tags in ein ID3-kompatibles Objekt."""
    from mutagen.id3 import TIT2, TPE1, TBPM, TKEY, TCON, COMM, WXXX
    tags.delall("TIT2")
    tags.add(TIT2(encoding=3, text=[str(kw["title"])]))
    tags.delall("TPE1")
    tags.add(TPE1(encoding=3, text=[str(kw["artist"])]))
    tags.delall("TCON")
    tags.add(TCON(encoding=3, text=[str(kw["genre"])]))
    if kw.get("bpm") is not None:
        tags.delall("TBPM")
        tags.add(TBPM(encoding=3, text=[str(int(round(kw["bpm"])))]))
    if kw.get("key_name"):
        tags.delall("TKEY")
        tags.add(TKEY(encoding=3, text=[str(kw["key_name"])]))
    if kw.get("comment"):
        tags.delall("COMM")
        tags.add(COMM(encoding=3, lang="deu", desc="sc-digger", text=[str(kw["comment"])]))
    if kw.get("url"):
        tags.delall("WXXX")
        tags.add(WXXX(encoding=3, desc="SoundCloud", url=str(kw["url"])))

    _replace_id3_loudness(tags, kw.get("loudness"))


def _tag_mp3(path: Path, **kw: object) -> None:
    from mutagen.id3 import ID3, TIT2, TPE1, TBPM, TKEY, TCON, COMM, WXXX, ID3NoHeaderError
    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        tags = ID3()

    tags.delall("TIT2")
    tags.add(TIT2(encoding=3, text=[str(kw["title"])]))
    tags.delall("TPE1")
    tags.add(TPE1(encoding=3, text=[str(kw["artist"])]))
    tags.delall("TCON")
    tags.add(TCON(encoding=3, text=[str(kw["genre"])]))
    if kw.get("bpm") is not None:
        tags.delall("TBPM")
        tags.add(TBPM(encoding=3, text=[str(int(round(kw["bpm"])))]))
    if kw.get("key_name"):
        tags.delall("TKEY")
        tags.add(TKEY(encoding=3, text=[str(kw["key_name"])]))
    if kw.get("comment"):
        tags.delall("COMM")
        tags.add(COMM(encoding=3, lang="deu", desc="sc-digger", text=[str(kw["comment"])]))
    if kw.get("url"):
        tags.delall("WXXX")
        tags.add(WXXX(encoding=3, desc="SoundCloud", url=str(kw["url"])))

    _replace_id3_loudness(tags, kw.get("loudness"))

    tags.save(str(path))


def _tag_flac(path: Path, **kw: object) -> None:
    from mutagen.flac import FLAC
    audio = FLAC(str(path))
    audio["title"] = str(kw["title"])
    audio["artist"] = str(kw["artist"])
    audio["genre"] = str(kw["genre"])
    if kw.get("bpm") is not None:
        audio["bpm"] = str(int(round(kw["bpm"])))
    if kw.get("key_name"):
        audio["initialkey"] = str(kw["key_name"])
    if kw.get("comment"):
        audio["comment"] = str(kw["comment"])
    if kw.get("url"):
        audio["url"] = str(kw["url"])

    for name in _LOUDNESS_TAG_KEYS:
        if name in audio:
            del audio[name]
    loudness = kw.get("loudness")
    loudness_dict = loudness_tag_values(loudness if isinstance(loudness, dict) else None)
    for name, val in loudness_dict.items():
        audio[name] = val

    audio.save()


def _tag_aiff(path: Path, **kw: object) -> None:
    from mutagen.aiff import AIFF
    audio = AIFF(str(path))
    if audio.tags is None:
        audio.add_tags()
    _fill_id3(audio.tags, kw)
    audio.save()


_INFO_CHUNK_ID_RE = re.compile(r"^[A-Z0-9]{4}$")
_STREAM_BUFFER_SIZE = 64 * 1024


def _copy_stream(src: BinaryIO, dst: BinaryIO, length: int) -> None:
    remaining = length
    while remaining > 0:
        chunk = src.read(min(remaining, _STREAM_BUFFER_SIZE))
        if not chunk:
            raise IOError("Unerwartetes Dateiende beim Lesen des Audio-Blocks")
        dst.write(chunk)
        remaining -= len(chunk)


def write_riff_info(path: Path, fields: dict[str, str | None]) -> bool:
    """Schreibt den LIST/INFO-Block einer WAV-Datei neu. Keys sind INFO-Chunk-IDs aus genau vier
    Zeichen A-Z/0-9 (z. B. INAM Titel, IART Artist, IGNR Genre, ICMT Kommentar); ein anderer
    Key -> ValueError (Programmierfehler, vor jedem Dateizugriff).

    - Leere Werte ("" oder None) werden weggelassen. Werte UTF-8 mit abschließendem NUL-Byte,
      jeder Unterblock auf gerade Länge mit einem NUL-Byte aufgefüllt (RIFF-Regel).
    - Alle vorhandenen LIST-Blöcke vom Typ INFO werden entfernt (z. B. vom Uploader). Alle anderen
      Blöcke (fmt, data, id3, LIST adtl, cue, bext, …) bleiben byte-identisch in ihrer Reihenfolge.
      Der neue INFO-Block kommt ans Ende. Die RIFF-Größe im Kopf wird neu berechnet.
    - Streamend arbeiten (Blöcke in Stücken kopieren, nie die ganze Datei oder den data-Block in
      den Speicher laden: Originale können mehrere hundert MB haben, der Server hat 1 GB RAM).
    - Atomar: in eine Temp-Datei IM SELBEN ORDNER schreiben (tempfile.mkstemp(dir=path.parent)),
      dann os.replace auf das Original. Bei jedem Fehler: Temp-Datei löschen, Original unverändert.
    - Keine gültige RIFF/WAVE-Datei (Kopf nicht "RIFF....WAVE", Block reicht über das Dateiende,
      abgeschnittener Blockkopf) -> False, Datei unverändert, keine Temp-Datei.
    - Rückgabe True bei Erfolg. Wirft außer dem ValueError nie; Fehler -> log.warning und False.
    """
    for k in fields:
        if not isinstance(k, str) or not _INFO_CHUNK_ID_RE.match(k):
            raise ValueError(f"Ungültige INFO-Chunk-ID: {k!r} (erwartet: genau 4 Zeichen A-Z/0-9)")

    path = Path(path)
    tmp_path: Path | None = None
    try:
        if not path.is_file():
            log.warning("WAV-Datei existiert nicht: %s", path)
            return False

        file_size = path.stat().st_size
        if file_size < 12:
            log.warning("WAV-Datei ist zu kurz für RIFF-Header (< 12 Bytes): %s", path)
            return False

        with open(path, "rb") as src:
            header = src.read(12)
            if len(header) < 12:
                return False
            riff_id, riff_size, wave_id = struct.unpack("<4sI4s", header)
            if riff_id != b"RIFF" or wave_id != b"WAVE":
                log.warning("Keine gültige RIFF/WAVE-Datei (Kopf %r/%r): %s", riff_id, wave_id, path)
                return False

            chunks: list[tuple[bytes, int, int, bool]] = []
            pos = 12
            while pos < file_size:
                if pos + 8 > file_size:
                    log.warning("WAV-Datei beschädigt (abgeschnittener Blockkopf bei Offset %d): %s", pos, path)
                    return False
                src.seek(pos)
                cid = src.read(4)
                chunk_size = struct.unpack("<I", src.read(4))[0]
                pad = chunk_size & 1
                if pos + 8 + chunk_size + pad > file_size:
                    log.warning("WAV-Datei beschädigt (Block %r reicht über Dateiende): %s", cid, path)
                    return False

                is_info = False
                if cid == b"LIST" and chunk_size >= 4:
                    list_type = src.read(4)
                    if list_type == b"INFO":
                        is_info = True

                chunks.append((cid, pos, chunk_size, is_info))
                pos += 8 + chunk_size + pad

            if pos != file_size:
                log.warning("WAV-Datei beschädigt (Chunk-Kette endet nicht am Dateiende): %s", path)
                return False

            # Neuer LIST/INFO-Block
            info_body = bytearray(b"INFO")
            for k, v in fields.items():
                if v is None or v == "":
                    continue
                val_bytes = str(v).encode("utf-8") + b"\x00"
                n = len(val_bytes)
                info_body.extend(k.encode("ascii"))
                info_body.extend(struct.pack("<I", n))
                info_body.extend(val_bytes)
                if n & 1:
                    info_body.append(0)

            new_info = b"LIST" + struct.pack("<I", len(info_body)) + bytes(info_body)

            # Temp-Datei im selben Ordner erzeugen
            fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".sc_digger_riff_", suffix=".tmp")
            tmp_path = Path(tmp_name)

            try:
                dst = open(fd, "wb")
            except Exception:
                os.close(fd)
                raise

            try:
                # RIFF-Header mit Platzhalter-Größe
                dst.write(b"RIFF\x00\x00\x00\x00WAVE")

                # Alle Nicht-INFO-Blöcke streamend kopieren
                for cid, chunk_pos, chunk_size, is_info in chunks:
                    if is_info:
                        continue
                    dst.write(cid)
                    dst.write(struct.pack("<I", chunk_size))
                    src.seek(chunk_pos + 8)
                    _copy_stream(src, dst, chunk_size)
                    if chunk_size & 1:
                        pad_byte = src.read(1)
                        dst.write(pad_byte if pad_byte else b"\x00")

                # Neuen INFO-Block ans Ende schreiben
                dst.write(new_info)

                # RIFF-Größe im Kopf berechnen und schreiben
                out_size = dst.tell()
                dst.seek(4)
                dst.write(struct.pack("<I", out_size - 8))
                dst.flush()
                os.fsync(dst.fileno())
            finally:
                dst.close()

        # src und dst sind jetzt geschlossen -> atomar ersetzen
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        log.warning("Fehler beim Schreiben von RIFF-INFO für %s: %s", path.name, e)
        if tmp_path is not None:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
        return False


def _tag_wav(path: Path, **kw: object) -> None:
    from mutagen.wave import WAVE
    audio = WAVE(str(path))
    if audio.tags is None:
        audio.add_tags()
    _fill_id3(audio.tags, kw)
    audio.save()

    title = kw.get("title")
    artist = kw.get("artist")
    genre = kw.get("genre")
    comment = kw.get("comment")
    fields = {
        "INAM": str(title) if title is not None else None,
        "IART": str(artist) if artist is not None else None,
        "IGNR": str(genre) if genre is not None else None,
        "ICMT": str(comment) if comment is not None else None,
    }
    ok = write_riff_info(path, fields)
    if not ok:
        raise RuntimeError("RIFF-INFO konnte nicht geschrieben werden")


def _tag_m4a(path: Path, **kw: object) -> None:
    from mutagen.mp4 import MP4
    audio = MP4(str(path))
    if audio.tags is None:
        audio.add_tags()
    audio["\xa9nam"] = [str(kw["title"])]
    audio["\xa9ART"] = [str(kw["artist"])]
    audio["\xa9gen"] = [str(kw["genre"])]
    if kw.get("bpm") is not None:
        audio["tmpo"] = [int(round(kw["bpm"]))]
    if kw.get("comment"):
        audio["\xa9cmt"] = [str(kw["comment"])]
    # M4A hat kein Standard-Key-Feld, nutzen wir ein Freitext-Feld
    if kw.get("key_name"):
        audio["\xa9cmt"] = [f"Key: {kw['key_name']} | {kw.get('comment', '')}".strip(" |")]

    if audio.tags:
        for k in list(audio.tags.keys()):
            if any(k.upper() == f"----:COM.APPLE.ITUNES:{name.upper()}" for name in _LOUDNESS_TAG_KEYS):
                del audio.tags[k]
    loudness = kw.get("loudness")
    loudness_dict = loudness_tag_values(loudness if isinstance(loudness, dict) else None)
    for name, val in loudness_dict.items():
        audio[f"----:com.apple.iTunes:{name}"] = [val.encode("utf-8")]

    audio.save()
