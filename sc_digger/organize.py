"""Auto-Organize: Sortiert heruntergeladene Tracks nach BPM/Key und schreibt ID3-Tags.

Tracks werden nach dem Schema inbox/<BPM-Range>/<Key>/ einsortiert und mit
vollständigen Metadaten getaggt, sodass sie in DJ-Software sofort nutzbar sind.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

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


def write_tags(path: Path, *, artist: str, title: str, bpm: float | None = None,
               key_name: str | None = None, genre: str = "Schranz",
               comment: str = "", url: str = "") -> bool:
    """Schreibt Metadaten-Tags in die Audio-Datei (ID3 für MP3, Vorbis für FLAC/OGG).

    Unterstützt: MP3, FLAC, AIFF, M4A. WAV wird übersprungen (kein Tag-Support).
    Gibt True zurück wenn Tags geschrieben wurden.
    """
    try:
        import mutagen
        from mutagen.easyid3 import EasyID3
        from mutagen.id3 import ID3, TBPM, TKEY, COMM, WXXX, ID3NoHeaderError
        from mutagen.flac import FLAC
        from mutagen.mp4 import MP4
        from mutagen.aiff import AIFF
    except ImportError:
        log.warning("mutagen nicht installiert – Tagging übersprungen")
        return False

    ext = path.suffix.lower()
    try:
        if ext == ".mp3":
            _tag_mp3(path, artist=artist, title=title, bpm=bpm,
                     key_name=key_name, genre=genre, comment=comment, url=url)
        elif ext == ".flac":
            _tag_flac(path, artist=artist, title=title, bpm=bpm,
                      key_name=key_name, genre=genre, comment=comment, url=url)
        elif ext in (".aif", ".aiff"):
            _tag_aiff(path, artist=artist, title=title, bpm=bpm,
                      key_name=key_name, genre=genre, comment=comment, url=url)
        elif ext == ".m4a":
            _tag_m4a(path, artist=artist, title=title, bpm=bpm,
                     key_name=key_name, genre=genre, comment=comment, url=url)
        elif ext == ".wav":
            log.debug("WAV-Tagging nicht unterstützt, übersprungen: %s", path.name)
            return False
        else:
            log.debug("Unbekanntes Format für Tagging: %s", ext)
            return False
        log.info("Tags geschrieben: %s", path.name)
        return True
    except Exception as e:
        log.warning("Tagging fehlgeschlagen für %s: %s", path.name, e)
        return False


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
    audio.save()


def _tag_aiff(path: Path, **kw: object) -> None:
    from mutagen.aiff import AIFF
    from mutagen.id3 import TIT2, TPE1, TBPM, TKEY, TCON, COMM, WXXX
    audio = AIFF(str(path))
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
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
    audio.save()


def _tag_m4a(path: Path, **kw: object) -> None:
    from mutagen.mp4 import MP4
    audio = MP4(str(path))
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
    audio.save()
