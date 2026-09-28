"""Akzeptanztests: WAV bekommt RIFF-INFO (Rekordbox liest bei WAV nur INFO, kein ID3)."""
import os
import struct
import subprocess
from pathlib import Path

import pytest

from sc_digger import organize
from sc_digger.organize import write_riff_info, write_tags


def _wav(path: Path, *meta: str) -> Path:
    """3 s Rauschen, 24 bit; meta z. B. "title=Uploader" erzeugt einen Uploader-INFO-Block."""
    args = [a for m in meta for a in ("-metadata", m)]
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=3:c=pink:r=44100:a=0.2",
                    "-ac", "2", "-c:a", "pcm_s24le", "-fflags", "+bitexact", *args, str(path)], check=True)
    return path


def chunks(path: Path) -> list[tuple[bytes, bytes]]:
    d = path.read_bytes()
    assert d[:4] == b"RIFF" and d[8:12] == b"WAVE"
    assert struct.unpack("<I", d[4:8])[0] == len(d) - 8, "RIFF-Größe stimmt nicht"
    out, i = [], 12
    while i < len(d):
        cid, n = d[i:i + 4], struct.unpack("<I", d[i + 4:i + 8])[0]
        out.append((cid, d[i + 8:i + 8 + n]))
        i += 8 + n + (n & 1)
    assert i == len(d), "Chunk-Kette endet nicht genau am Dateiende"
    return out


def info(path: Path) -> dict[str, str]:
    lists = [body for cid, body in chunks(path) if cid == b"LIST" and body[:4] == b"INFO"]
    assert len(lists) <= 1, "mehr als ein INFO-Block"
    out, body, i = {}, (lists[0] if lists else b"INFO"), 4
    while i < len(body):
        k, n = body[i:i + 4].decode(), struct.unpack("<I", body[i + 4:i + 8])[0]
        out[k] = body[i + 8:i + 8 + n].rstrip(b"\0").decode("utf-8")
        i += 8 + n + (n & 1)
    return out


def data(path: Path) -> bytes:
    return next(body for cid, body in chunks(path) if cid == b"data")


FIELDS = {"INAM": "NØSS – Join me", "IART": "sc-digger Artist", "IGNR": "Schranz", "ICMT": "Score: 87p | Key: 8A"}


def test_writes_info_and_keeps_audio(tmp_path):
    p = _wav(tmp_path / "a.wav")
    audio = data(p)
    assert write_riff_info(p, FIELDS) is True
    assert info(p) == FIELDS
    assert data(p) == audio


def test_replaces_uploader_info(tmp_path):
    p = _wav(tmp_path / "a.wav", "title=Uploader Titel", "artist=Uploader Artist")
    assert info(p)["INAM"] == "Uploader Titel"
    assert write_riff_info(p, FIELDS)
    assert info(p) == FIELDS
    assert b"Uploader" not in p.read_bytes()


def test_other_chunks_are_kept_unchanged(tmp_path):
    p = _wav(tmp_path / "a.wav")
    write_tags(p, artist="A", title="T")            # erzeugt den ID3-Block wie im Betrieb
    before = {cid: body for cid, body in chunks(p) if cid != b"LIST"}
    assert b"id3 " in before
    assert write_riff_info(p, FIELDS)
    after = {cid: body for cid, body in chunks(p) if cid != b"LIST"}
    assert after == before


def test_odd_lengths_are_padded_and_empty_values_skipped(tmp_path):
    p = _wav(tmp_path / "a.wav")
    assert write_riff_info(p, {"INAM": "Abc", "IART": "", "IGNR": None, "ICMT": "Ö"})
    assert info(p) == {"INAM": "Abc", "ICMT": "Ö"}


def test_rewriting_twice_gives_one_info_block(tmp_path):
    p = _wav(tmp_path / "a.wav")
    write_riff_info(p, {"INAM": "Erst"})
    write_riff_info(p, {"INAM": "Dann"})
    assert info(p) == {"INAM": "Dann"}


@pytest.mark.parametrize("content", [b"", b"RIFF", b"RIFF\x10\x00\x00\x00AVI LIST", b"ID3\x03\x00junk"])
def test_invalid_file_is_left_untouched(tmp_path, content):
    p = tmp_path / "kaputt.wav"
    p.write_bytes(content)
    assert write_riff_info(p, FIELDS) is False
    assert p.read_bytes() == content
    assert [x.name for x in tmp_path.iterdir()] == ["kaputt.wav"]


def test_failure_keeps_original_and_leaves_no_temp_file(tmp_path, monkeypatch):
    p = _wav(tmp_path / "a.wav", "title=Uploader Titel")
    original = p.read_bytes()

    def boom(*a, **k):
        raise OSError("Datenträger voll")
    monkeypatch.setattr(os, "replace", boom)
    assert write_riff_info(p, FIELDS) is False
    assert p.read_bytes() == original
    assert [x.name for x in tmp_path.iterdir()] == ["a.wav"]


def test_rejects_invalid_chunk_ids(tmp_path):
    p = _wav(tmp_path / "a.wav")
    with pytest.raises(ValueError):
        write_riff_info(p, {"TITLE": "x"})


def test_write_tags_fills_info_for_wav(tmp_path):
    p = _wav(tmp_path / "a.wav", "title=Uploader Titel", "artist=Uploader Artist")
    audio = data(p)
    assert write_tags(p, artist="sc-digger Artist", title="Neuer Titel", genre="Schranz",
                      comment="Score: 87p", bpm=156) is True
    assert info(p) == {"INAM": "Neuer Titel", "IART": "sc-digger Artist", "IGNR": "Schranz", "ICMT": "Score: 87p"}
    assert data(p) == audio
    import mutagen
    assert mutagen.File(str(p)).tags["TIT2"].text[0] == "Neuer Titel"   # ID3 bleibt für andere Software


def test_write_tags_reports_failure_when_info_cannot_be_written(tmp_path, monkeypatch):
    p = _wav(tmp_path / "a.wav")
    monkeypatch.setattr(organize, "write_riff_info", lambda *a, **k: False)
    assert write_tags(p, artist="A", title="T") is False
