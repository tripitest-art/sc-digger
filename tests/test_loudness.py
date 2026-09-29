"""Unit- und Integrationstests für sc_digger/loudness.py (Issue #74)."""
from __future__ import annotations

import ast
import os
from pathlib import Path
import struct

import numpy as np
import pytest
import soundfile as sf
import yaml

from sc_digger import loudness
from sc_digger import output as out
from sc_digger.loudness import apply_gain, normalize_inbox_file, plan_gain
from sc_digger.models import Config
from tests.test_modes import mk

ROOT = Path(__file__).resolve().parents[1]
SR = 44100


def _factor(db: float) -> float:
    return 10.0 ** (db / 20.0)


def _signal(seconds: float = 1.0, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    left = amp * np.sin(2 * np.pi * 440 * t)
    right = amp * np.sin(2 * np.pi * 880 * t)
    return np.stack([left, right], axis=1)


def _chunk(cid: bytes, payload: bytes, big: bool = False) -> bytes:
    size = struct.pack(">I" if big else "<I", len(payload))
    return cid + size + payload + (b"\x00" if len(payload) % 2 else b"")


def _chunks(data: bytes, big: bool = False) -> list[tuple[bytes, bytes]]:
    res, pos = [], 12
    while pos + 8 <= len(data):
        cid = data[pos : pos + 4]
        (size,) = struct.unpack(">I" if big else "<I", data[pos + 4 : pos + 8])
        res.append((cid, data[pos + 8 : pos + 8 + size]))
        pos += 8 + size + (size % 2)
    return res


def _to24_le(x: np.ndarray) -> bytes:
    i = np.round(x * (2**23 - 1)).astype("<i4")
    return i.reshape(-1).view(np.uint8).reshape(-1, 4)[:, :3].tobytes()


def _from24_le(b: bytes) -> np.ndarray:
    a = np.frombuffer(b, np.uint8).reshape(-1, 3).astype(np.int32)
    i = a[:, 0] | (a[:, 1] << 8) | (a[:, 2] << 16)
    return np.where(i >= 1 << 23, i - (1 << 24), i)


def _to24_be(x: np.ndarray) -> bytes:
    i = np.round(x * (2**23 - 1)).astype(">i4")
    return i.reshape(-1).view(np.uint8).reshape(-1, 4)[:, 1:4].tobytes()


def _from24_be(b: bytes) -> np.ndarray:
    a = np.frombuffer(b, np.uint8).reshape(-1, 3).astype(np.int32)
    i = (a[:, 0] << 16) | (a[:, 1] << 8) | a[:, 2]
    return np.where(i >= 1 << 23, i - (1 << 24), i)


def make_aiff24(path: Path, x: np.ndarray) -> None:
    ch, n = x.shape[1], x.shape[0]
    sr80 = b"\x40\x0e\xac\x44\x00\x00\x00\x00\x00\x00"
    pcm = _to24_be(x)
    body = (
        b"AIFF"
        + _chunk(b"COMM", struct.pack(">hIh", ch, n, 24) + sr80, big=True)
        + _chunk(b"MARK", b"\x00\x01\x00\x01\x00\x00V\"\x04Cue1", big=True)
        + _chunk(b"SSND", struct.pack(">II", 0, 0) + pcm, big=True)
        + _chunk(b"ID3 ", b"ID3\x04\x00\x00\x00\x00\x00\x07GEOB-ok", big=True)
    )
    path.write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)


def make_wav_extensible(path: Path, x: np.ndarray, bits: int = 24) -> None:
    ch = x.shape[1]
    bytes_per_sample = bits // 8
    block_align = ch * bytes_per_sample
    avg_bytes = SR * block_align
    subformat_pcm = b"\x01\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
    # wFormatTag=0xFFFE, nChannels, nSamplesPerSec, nAvgBytesPerSec, nBlockAlign, wBitsPerSample
    # cbSize=22, wValidBitsPerSample, dwChannelMask, SubFormat
    fmt_payload = (
        struct.pack("<HHIIHH", 0xFFFE, ch, SR, avg_bytes, block_align, bits)
        + struct.pack("<HHI", 22, bits, 3)
        + subformat_pcm
    )
    data_payload = _to24_le(x) if bits == 24 else (np.round(x * 32767).astype("<i2").tobytes())
    body = (
        b"WAVE"
        + _chunk(b"fmt ", fmt_payload)
        + _chunk(b"bext", b"odd!!")
        + _chunk(b"data", data_payload)
        + _chunk(b"id3 ", b"ID3\x04\x00\x00\x00\x00\x00\x07GEOB-ok")
    )
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


# ---------------- AIFF 24 Bit ----------------
def test_aiff_24bit(tmp_path):
    f = tmp_path / "t24.aiff"
    x = _signal(amp=0.4)
    make_aiff24(f, x)
    before = f.read_bytes()

    assert apply_gain(f, 3.0) is True
    after = f.read_bytes()

    assert len(after) == len(before)
    cb, ca = _chunks(before, big=True), _chunks(after, big=True)
    assert [c for c, _ in ca] == [c for c, _ in cb]
    for (cid, pb), (_, pa) in zip(cb, ca):
        if cid == b"SSND":
            assert pa[:8] == pb[:8] and len(pa) == len(pb)
        else:
            assert pa == pb, cid
    old = _from24_be(dict(cb)[b"SSND"][8:])
    new = _from24_be(dict(ca)[b"SSND"][8:])
    assert np.max(np.abs(new - np.round(old * _factor(3.0)))) <= 2


# ---------------- WAV mit WAVE_FORMAT_EXTENSIBLE ----------------
def test_wav_extensible_24bit(tmp_path):
    f = tmp_path / "t_ext.wav"
    x = _signal()
    make_wav_extensible(f, x, bits=24)
    before = f.read_bytes()

    assert apply_gain(f, -4.0) is True
    after = f.read_bytes()

    assert len(after) == len(before)
    cb, ca = _chunks(before), _chunks(after)
    assert [c for c, _ in ca] == [c for c, _ in cb]
    for (cid, pb), (_, pa) in zip(cb, ca):
        if cid == b"data":
            assert len(pa) == len(pb)
        else:
            assert pa == pb, cid
    old = _from24_le(dict(cb)[b"data"])
    new = _from24_le(dict(ca)[b"data"])
    assert np.max(np.abs(new - np.round(old * _factor(-4.0)))) <= 2


# ---------------- FLAC 24 Bit ----------------
def test_flac_24bit(tmp_path):
    from mutagen.flac import FLAC, Picture

    f = tmp_path / "t24.flac"
    x = _signal()
    sf.write(str(f), x, SR, subtype="PCM_24", format="FLAC")
    mf = FLAC(str(f))
    mf["TITLE"] = "24bit FLAC"
    mf["SERATO_MARKERS_V2"] = "testmarker"
    pic = Picture()
    pic.type, pic.mime, pic.data = 3, "image/png", b"\x89PNG-test24"
    mf.add_picture(pic)
    mf.save()

    before = f.read_bytes()
    old_32, _ = sf.read(str(f), dtype="int32")
    old_24 = old_32 >> 8

    assert apply_gain(f, -3.0) is True
    after = f.read_bytes()

    new_32, sr = sf.read(str(f), dtype="int32")
    new_24 = new_32 >> 8
    info = sf.info(str(f))

    assert sr == SR and info.channels == 2 and info.subtype == "PCM_24"
    assert new_24.shape == old_24.shape
    assert np.max(np.abs(new_24.astype(np.int64) - np.round(old_24 * _factor(-3.0)))) <= 2


# ---------------- Übersteuerung: Sättigung statt Umklappen ----------------
def test_saturation_no_overflow_16bit(tmp_path):
    f = tmp_path / "sat16.wav"
    # Hohe Amplitude 0.95 -> Werte ~ 31128
    t = np.arange(int(SR * 0.1)) / SR
    left = 0.95 * np.sin(2 * np.pi * 100 * t)
    right = 0.95 * np.sin(2 * np.pi * 100 * t)
    sig = np.stack([left, right], axis=1)
    # 16-Bit WAV
    ch = sig.shape[1]
    pcm = np.round(sig * 32767).astype("<i2").tobytes()
    body = (
        b"WAVE"
        + _chunk(b"fmt ", struct.pack("<HHIIHH", 1, ch, SR, SR * ch * 2, ch * 2, 16))
        + _chunk(b"data", pcm)
    )
    f.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)

    # +12 dB entspricht ca. Faktor 3.98 -> würde ohne Sättigung drastisch umklappen
    assert apply_gain(f, 12.0) is True

    after = f.read_bytes()
    new_samples = np.frombuffer(dict(_chunks(after))[b"data"], dtype="<i2")
    assert new_samples.max() == 32767
    assert new_samples.min() == -32768
    # Positive Peaks müssen positiv bleiben (nicht negativ umklappen)
    peak_idx = np.argmax(sig[:, 0])
    assert new_samples.reshape(-1, 2)[peak_idx, 0] == 32767


def test_saturation_no_overflow_24bit(tmp_path):
    f = tmp_path / "sat24.wav"
    sig = np.ones((100, 2), dtype=np.float64) * 0.9  # Konstante positive Werte
    body = (
        b"WAVE"
        + _chunk(b"fmt ", struct.pack("<HHIIHH", 1, 2, SR, SR * 2 * 3, 2 * 3, 24))
        + _chunk(b"data", _to24_le(sig))
    )
    f.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)

    # +6 dB -> ca. Faktor 2.0 -> Werte > 8388607
    assert apply_gain(f, 6.0) is True

    after = f.read_bytes()
    new_samples = _from24_le(dict(_chunks(after))[b"data"])
    assert new_samples.max() == 8388607
    assert np.all(new_samples == 8388607)


# ---------------- Keine Temp-Dateien nach Schreibfehler ----------------
def test_write_error_leaves_no_temp_files(tmp_path, monkeypatch):
    f = tmp_path / "err.wav"
    sig = _signal(0.2)
    body = (
        b"WAVE"
        + _chunk(b"fmt ", struct.pack("<HHIIHH", 1, 2, SR, SR * 2 * 2, 2 * 2, 16))
        + _chunk(b"data", np.round(sig * 32767).astype("<i2").tobytes())
    )
    f.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    before = f.read_bytes()

    # Fehler beim os.replace provozieren
    def _fail_replace(src, dst):
        raise OSError("Simulierter I/O-Fehler beim atomaren Ersetzen")

    monkeypatch.setattr(os, "replace", _fail_replace)

    assert apply_gain(f, -3.0) is False
    assert f.read_bytes() == before
    # Keine Reste
    assert sorted(p.name for p in tmp_path.iterdir()) == ["err.wav"]


def test_flac_encoder_error_leaves_no_temp_files(tmp_path, monkeypatch):
    f = tmp_path / "err.flac"
    sig = _signal(0.2)
    sf.write(str(f), sig, SR, subtype="PCM_16", format="FLAC")
    before = f.read_bytes()

    def _fail_write(*args, **kwargs):
        raise RuntimeError("Simulierter Encoder-Absturz")

    monkeypatch.setattr(sf, "write", _fail_write)

    assert apply_gain(f, -3.0) is False
    assert f.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["err.flac"]


# ---------------- Aufrufhierarchie: apply_gain & normalize_inbox_file ----------------
def test_call_hierarchy_enforced():
    """Stellt sicher:
    - apply_gain wird nur aus normalize_inbox_file aufgerufen
    - normalize_inbox_file wird nur aus finalize_quality aufgerufen
    """
    sc_digger_dir = ROOT / "sc_digger"
    apply_gain_callers = []
    normalize_callers = []

    for py_file in sc_digger_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = None
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name == "apply_gain":
                    apply_gain_callers.append((py_file.name, node.lineno))
                elif name == "normalize_inbox_file":
                    normalize_callers.append((py_file.name, node.lineno))

    # apply_gain darf im Produktivcode nur in sc_digger/loudness.py aufgerufen werden
    caller_files_apply = {f for f, _ in apply_gain_callers}
    assert caller_files_apply == {"loudness.py"}, f"Unerlaubte Aufrufer von apply_gain: {apply_gain_callers}"

    # normalize_inbox_file darf im Produktivcode nur in sc_digger/output.py aufgerufen werden
    caller_files_norm = {f for f, _ in normalize_callers}
    assert caller_files_norm == {"output.py"}, f"Unerlaubte Aufrufer von normalize_inbox_file: {normalize_callers}"


# ---------------- WAV / AIFF 32-Bit ----------------
def test_wav_32bit_pcm(tmp_path):
    f = tmp_path / "t32.wav"
    sig = _signal(0.2)
    ch = sig.shape[1]
    pcm = np.round(sig * 2147483647).astype("<i4").tobytes()
    body = (
        b"WAVE"
        + _chunk(b"fmt ", struct.pack("<HHIIHH", 1, ch, SR, SR * ch * 4, ch * 4, 32))
        + _chunk(b"data", pcm)
    )
    f.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)

    assert apply_gain(f, -6.0) is True
    after = f.read_bytes()
    new_samples = np.frombuffer(dict(_chunks(after))[b"data"], dtype="<i4")
    old_samples = np.frombuffer(pcm, dtype="<i4")
    assert np.max(np.abs(new_samples - np.round(old_samples * _factor(-6.0)))) <= 2


# ---------------- Nicht unterstützte WAV-Formate ----------------
def test_unsupported_wav_ieee_float(tmp_path):
    f = tmp_path / "float.wav"
    sig = _signal(0.2).astype(np.float32)
    ch = sig.shape[1]
    body = (
        b"WAVE"
        + _chunk(b"fmt ", struct.pack("<HHIIHH", 3, ch, SR, SR * ch * 4, ch * 4, 32))  # wFormatTag=3 (IEEE Float)
        + _chunk(b"data", sig.tobytes())
    )
    f.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    before = f.read_bytes()

    assert apply_gain(f, -3.0) is False
    assert f.read_bytes() == before


def test_unsupported_wav_extensible_non_pcm(tmp_path):
    f = tmp_path / "ext_non_pcm.wav"
    sig = _signal(0.2)
    ch = sig.shape[1]
    subformat_float = b"\x03\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
    fmt_payload = (
        struct.pack("<HHIIHH", 0xFFFE, ch, SR, SR * ch * 4, ch * 4, 32)
        + struct.pack("<HHI", 22, 32, 3)
        + subformat_float
    )
    body = b"WAVE" + _chunk(b"fmt ", fmt_payload) + _chunk(b"data", sig.astype(np.float32).tobytes())
    f.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    before = f.read_bytes()

    assert apply_gain(f, -3.0) is False
    assert f.read_bytes() == before
