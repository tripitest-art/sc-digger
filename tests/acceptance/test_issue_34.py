"""Akzeptanztests: RIFF-INFO auch bei WAVs, deren letzter Block ohne Füllbyte endet."""
import struct
from pathlib import Path

from sc_digger.organize import write_riff_info

FMT = struct.pack("<HHIIHH", 1, 1, 8000, 8000, 1, 8)
DATA = bytes(range(256)) * 31 + bytes(63)          # 7999 Bytes: ungerade Länge


def wav(pad: bool, trailing_list: bool = False) -> bytes:
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + FMT
    if trailing_list:
        body += b"data" + struct.pack("<I", len(DATA)) + DATA + b"\0"
        lst = b"adtlxyz"                              # 7 Bytes: ungerade
        body += b"LIST" + struct.pack("<I", len(lst)) + lst + (b"\0" if pad else b"")
    else:
        body += b"data" + struct.pack("<I", len(DATA)) + DATA + (b"\0" if pad else b"")
    return b"RIFF" + struct.pack("<I", len(body)) + body


def chunks(d: bytes) -> list[tuple[bytes, bytes]]:
    assert struct.unpack("<I", d[4:8])[0] == len(d) - 8
    out, i = [], 12
    while i < len(d):
        cid, n = d[i:i + 4], struct.unpack("<I", d[i + 4:i + 8])[0]
        out.append((cid, d[i + 8:i + 8 + n]))
        i += 8 + n + (n & 1)
    assert i == len(d), "Kette endet nicht genau am Dateiende (Füllbyte fehlt?)"
    return out


def test_missing_pad_on_last_block_is_tolerated_and_repaired(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(wav(pad=False))
    assert write_riff_info(p, {"INAM": "Titel"}) is True
    out = chunks(p.read_bytes())
    assert [c for c, _ in out] == [b"fmt ", b"data", b"LIST"]
    assert dict(out)[b"data"] == DATA


def test_missing_pad_on_last_non_data_block_is_tolerated(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(wav(pad=False, trailing_list=True))
    assert write_riff_info(p, {"INAM": "Titel"}) is True
    out = chunks(p.read_bytes())
    assert [c for c, _ in out] == [b"fmt ", b"data", b"LIST", b"LIST"]
    assert out[2][1] == b"adtlxyz"


def test_with_pad_still_works(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(wav(pad=True))
    assert write_riff_info(p, {"INAM": "Titel"}) is True
    assert dict(chunks(p.read_bytes()))[b"data"] == DATA


def test_block_really_longer_than_file_is_still_rejected(tmp_path):
    p = tmp_path / "a.wav"
    broken = wav(pad=True)[:-100]                    # 100 Bytes abgeschnitten, nicht nur das Füllbyte
    p.write_bytes(broken)
    assert write_riff_info(p, {"INAM": "Titel"}) is False
    assert p.read_bytes() == broken
