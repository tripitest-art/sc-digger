"""Akzeptanztests: Inbox-Normalisierung auf -8.5 LUFS, samplegenau und ohne Metadaten-Verlust."""
import struct
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
import yaml

from sc_digger import output as out
from sc_digger.loudness import apply_gain, plan_gain
from sc_digger.models import Config
from tests.test_modes import mk

ROOT = Path(__file__).resolve().parents[2]
SR = 44100


# ---------------- Hilfen: Testdateien Byte für Byte selbst bauen ----------------
def _chunk(cid: bytes, payload: bytes, big: bool = False) -> bytes:
    size = struct.pack(">I" if big else "<I", len(payload))
    return cid + size + payload + (b"\x00" if len(payload) % 2 else b"")


def _chunks(data: bytes, big: bool = False) -> list[tuple[bytes, bytes]]:
    """(ID, Nutzdaten) aller Unter-Chunks eines RIFF-/FORM-Containers, in Dateireihenfolge."""
    res, pos = [], 12
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        (size,) = struct.unpack(">I" if big else "<I", data[pos + 4:pos + 8])
        res.append((cid, data[pos + 8:pos + 8 + size]))
        pos += 8 + size + (size % 2)
    return res


def _signal(seconds: float = 2.0, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    left = amp * (0.7 * np.sin(2 * np.pi * 440 * t) + 0.3 * np.sin(2 * np.pi * 1000 * t))
    right = amp * (0.6 * np.sin(2 * np.pi * 330 * t) + 0.4 * np.sin(2 * np.pi * 2500 * t))
    return np.stack([left, right], axis=1)


def _to24(x: np.ndarray) -> bytes:
    i = np.round(x * (2 ** 23 - 1)).astype("<i4")
    return i.reshape(-1).view(np.uint8).reshape(-1, 4)[:, :3].tobytes()


def _from24(b: bytes) -> np.ndarray:
    a = np.frombuffer(b, np.uint8).reshape(-1, 3).astype(np.int32)
    i = a[:, 0] | (a[:, 1] << 8) | (a[:, 2] << 16)
    return np.where(i >= 1 << 23, i - (1 << 24), i)


def make_wav24(path: Path, x: np.ndarray) -> None:
    """WAV 24 Bit mit Fremd-Chunks vor und hinter den Audiodaten (wie Rekordbox/Serato/BWF)."""
    ch = x.shape[1]
    body = (b"WAVE"
            + _chunk(b"fmt ", struct.pack("<HHIIHH", 1, ch, SR, SR * ch * 3, ch * 3, 24))
            + _chunk(b"bext", b"odd!!")                               # ungerade Länge -> Pad-Byte
            + _chunk(b"cue ", struct.pack("<I", 1) + b"C1\x00\x00" + bytes(16) + struct.pack("<I", 22050))
            + _chunk(b"data", _to24(x))
            + _chunk(b"id3 ", b"ID3\x04\x00\x00\x00\x00\x00\x07GEOB-ok")
            + _chunk(b"LIST", b"INFOISFT\x05\x00\x00\x00test\x00\x00"))
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def make_aiff16(path: Path, x: np.ndarray) -> None:
    """AIFF 16 Bit mit MARK- und ID3-Chunk."""
    ch, n = x.shape[1], x.shape[0]
    sr80 = b"\x40\x0e\xac\x44\x00\x00\x00\x00\x00\x00"                # 44100 Hz, 80-Bit-Extended
    pcm = np.round(x * 32767).astype(">i2").tobytes()
    body = (b"AIFF"
            + _chunk(b"COMM", struct.pack(">hIh", ch, n, 16) + sr80, big=True)
            + _chunk(b"MARK", b"\x00\x01\x00\x01\x00\x00V\"\x04Cue1", big=True)  # ungerade Länge
            + _chunk(b"SSND", struct.pack(">II", 0, 0) + pcm, big=True)
            + _chunk(b"ID3 ", b"ID3\x04\x00\x00\x00\x00\x00\x07GEOB-ok", big=True))
    path.write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)


def _flac_blocks(data: bytes) -> list[tuple[int, bytes]]:
    assert data[:4] == b"fLaC"
    res, pos, last = [], 4, False
    while not last:
        hdr = data[pos]
        last, btype = bool(hdr & 0x80), hdr & 0x7F
        size = int.from_bytes(data[pos + 1:pos + 4], "big")
        res.append((btype, data[pos + 4:pos + 4 + size]))
        pos += 4 + size
    return res


def make_flac16(path: Path, x: np.ndarray) -> None:
    """FLAC 16 Bit mit Vorbis-Kommentaren (inkl. Serato-Feld) und eingebettetem Cover."""
    from mutagen.flac import FLAC, Picture
    sf.write(str(path), x, SR, subtype="PCM_16", format="FLAC")
    f = FLAC(str(path))
    f["TITLE"] = "Testtrack"
    f["BPM"] = "158"
    f["SERATO_MARKERS_V2"] = "YXBwbGljYXRpb24vb2N0ZXQtc3RyZWFt"
    pic = Picture()
    pic.type, pic.mime, pic.data = 3, "image/png", b"\x89PNG-test"
    f.add_picture(pic)
    f.save()


def _factor(db: float) -> float:
    return 10 ** (db / 20)


# ---------------- plan_gain ----------------
@pytest.mark.parametrize("lufs, tp, expected", [
    (-5.0, 1.0, -3.5),      # zu laut: nur absenken, True Peak spielt keine Rolle
    (-5.0, None, -3.5),     # Absenken braucht keinen Peak-Messwert
    (-8.6, 0.0, 0.0),       # innerhalb ±0.2 LU: nichts tun
    (-8.35, 0.0, 0.0),
    (-11.0, -3.0, 2.5),     # zu leise, genug Headroom: bis zum Ziel anheben
    (-11.0, -1.5, 1.0),     # zu leise, Headroom reicht nur für +1.0 dB (kein Limiter)
    (-11.0, 0.5, 0.0),      # zu leise, Peak schon über -0.5 dBTP: nicht anfassen
    (-11.0, None, 0.0),     # Anheben ohne Peak-Messwert: nicht anfassen
    (None, 0.0, 0.0),       # keine Messung: nicht anfassen
])
def test_plan_gain(lufs, tp, expected):
    assert plan_gain(lufs, tp) == pytest.approx(expected, abs=1e-9)


def test_plan_gain_custom_target():
    assert plan_gain(-10.0, -6.0, target_lufs=-12.0) == pytest.approx(-2.0)
    assert plan_gain(-14.0, -4.0, target_lufs=-12.0, max_true_peak_dbfs=-1.0) == pytest.approx(2.0)
    assert plan_gain(-14.0, -2.5, target_lufs=-12.0, max_true_peak_dbfs=-1.0) == pytest.approx(1.5)


# ---------------- apply_gain: WAV ----------------
def test_wav_samplegenau_und_alle_chunks_unveraendert(tmp_path):
    f = tmp_path / "t.wav"
    x = _signal()
    make_wav24(f, x)
    before = f.read_bytes()

    assert apply_gain(f, -6.0) is True
    after = f.read_bytes()

    assert len(after) == len(before)
    cb, ca = _chunks(before), _chunks(after)
    assert [c for c, _ in ca] == [c for c, _ in cb]                   # gleiche Chunks, gleiche Reihenfolge
    for (cid, pb), (_, pa) in zip(cb, ca):
        if cid == b"data":
            assert len(pa) == len(pb)
        else:
            assert pa == pb, cid                                        # byte-identisch
    old = _from24(dict(cb)[b"data"])
    new = _from24(dict(ca)[b"data"])
    # Toleranz 2 LSB (Rundung + Dither). Ein Versatz um nur ein Sample läge weit darüber.
    assert np.max(np.abs(new - np.round(old * _factor(-6.0)))) <= 2


# ---------------- apply_gain: AIFF ----------------
def test_aiff_samplegenau_und_alle_chunks_unveraendert(tmp_path):
    f = tmp_path / "t.aiff"
    x = _signal(amp=0.4)
    make_aiff16(f, x)
    before = f.read_bytes()

    assert apply_gain(f, 3.0) is True                                   # Anheben, ohne zu übersteuern
    after = f.read_bytes()

    assert len(after) == len(before)
    cb, ca = _chunks(before, big=True), _chunks(after, big=True)
    assert [c for c, _ in ca] == [c for c, _ in cb]
    for (cid, pb), (_, pa) in zip(cb, ca):
        if cid == b"SSND":
            assert pa[:8] == pb[:8] and len(pa) == len(pb)             # offset/blockSize unverändert
        else:
            assert pa == pb, cid
    old = np.frombuffer(dict(cb)[b"SSND"][8:], ">i2").astype(np.int64)
    new = np.frombuffer(dict(ca)[b"SSND"][8:], ">i2").astype(np.int64)
    assert np.max(np.abs(new - np.round(old * _factor(3.0)))) <= 2


# ---------------- apply_gain: FLAC ----------------
def test_flac_metadaten_bloecke_byte_identisch(tmp_path):
    f = tmp_path / "t.flac"
    x = _signal()
    make_flac16(f, x)
    before = f.read_bytes()
    old, _ = sf.read(str(f), dtype="int16")

    assert apply_gain(f, -6.0) is True
    after = f.read_bytes()

    # Alles außer STREAMINFO (0), PADDING (1) und SEEKTABLE (3, Byte-Offsets ändern sich) bleibt gleich
    keep = lambda blocks: [(t, b) for t, b in blocks if t not in (0, 1, 3)]
    assert keep(_flac_blocks(after)) == keep(_flac_blocks(before))
    assert _flac_blocks(after)[0][0] == 0                               # STREAMINFO bleibt erster Block

    new, sr = sf.read(str(f), dtype="int16")
    info = sf.info(str(f))
    assert sr == SR and info.channels == 2 and info.subtype == "PCM_16"
    assert new.shape == old.shape                                       # gleiche Sample-Anzahl
    assert np.max(np.abs(new.astype(np.int64) - np.round(old * _factor(-6.0)))) <= 2


# ---------------- apply_gain: nichts tun, nie werfen ----------------
@pytest.mark.parametrize("name, content", [
    ("t.mp3", b"\xff\xfb\x90\x00" + bytes(2000)),    # MP3: nicht Teil dieser Aufgabe
    ("t.m4a", b"\x00\x00\x00\x18ftypM4A " + bytes(100)),
    ("t.wav", b"RIFF\xff\xff\x00\x00WAVEfmt "),       # kaputt / abgeschnitten
    ("t.flac", b"fLaC\x00\x00"),                        # kaputt
])
def test_unsupported_or_broken_untouched(tmp_path, name, content):
    f = tmp_path / name
    f.write_bytes(content)
    assert apply_gain(f, -3.0) is False
    assert f.read_bytes() == content
    assert sorted(p.name for p in tmp_path.iterdir()) == [name]       # keine Temp-Reste


def test_zero_gain_untouched(tmp_path):
    f = tmp_path / "t.wav"
    make_wav24(f, _signal())
    before = f.read_bytes()
    assert apply_gain(f, 0.0) is False
    assert f.read_bytes() == before


# ---------------- finalize_quality ----------------
def _cfg(tmp_path, loudness):
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    raw["fingerprint"] = {"check_downloads": False}
    raw["state"] = {"db_path": str(tmp_path / "s.sqlite"), "track_db_path": str(tmp_path / "t.sqlite")}
    if loudness is None:
        raw.pop("loudness", None)
    else:
        raw["loudness"] = loudness
    return Config(raw)


def _report():
    return {"ok": True, "clipped": False, "integrated_lufs": -5.0,
            "true_peak_dbfs": 1.0, "loudness_range_lu": 6.0}


def test_finalize_quality_normalisiert_inbox_download(tmp_path, monkeypatch):
    f = tmp_path / "t.wav"
    x = _signal()
    make_wav24(f, x)
    old = _from24(dict(_chunks(f.read_bytes()))[b"data"])
    monkeypatch.setattr(out, "check_file", lambda p, cfg: _report())
    cfg = _cfg(tmp_path, {"normalize_inbox": True, "target_lufs": -8.5, "max_true_peak_dbfs": -0.5})
    t = mk(1)

    assert out.finalize_quality(t, f, tmp_path, cfg) == f
    q = t.quality_report
    assert q["gain_applied_db"] == pytest.approx(-3.5)
    assert q["integrated_lufs"] == pytest.approx(-8.5)
    assert q["true_peak_dbfs"] == pytest.approx(-2.5)
    assert q["loudness_range_lu"] == pytest.approx(6.0)                 # Dynamik unverändert
    new = _from24(dict(_chunks(f.read_bytes()))[b"data"])
    assert np.max(np.abs(new - np.round(old * _factor(-3.5)))) <= 2


@pytest.mark.parametrize("loudness", [None, {"normalize_inbox": False, "target_lufs": -8.5}])
def test_finalize_quality_ohne_normalisierung(tmp_path, monkeypatch, loudness):
    f = tmp_path / "t.wav"
    make_wav24(f, _signal())
    before = f.read_bytes()
    monkeypatch.setattr(out, "check_file", lambda p, cfg: _report())
    t = mk(1)

    assert out.finalize_quality(t, f, tmp_path, _cfg(tmp_path, loudness)) == f
    assert f.read_bytes() == before
    assert "gain_applied_db" not in t.quality_report
    assert t.quality_report["integrated_lufs"] == pytest.approx(-5.0)


def test_config_defaults():
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert raw["loudness"]["normalize_inbox"] is True
    assert raw["loudness"]["target_lufs"] == -8.5
    assert raw["loudness"]["max_true_peak_dbfs"] == -0.5