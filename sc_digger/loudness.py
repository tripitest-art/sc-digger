"""Lautheits-Normalisierung für Downloads in der Inbox (-8.5 LUFS, samplegenau)."""
from __future__ import annotations

import logging
import os
from pathlib import Path
import struct
import uuid

import numpy as np
import soundfile as sf

from sc_digger.models import Config

log = logging.getLogger(__name__)

SUBFORMAT_PCM = b"\x01\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"


def plan_gain(
    lufs: float | None,
    true_peak_dbfs: float | None,
    *,
    target_lufs: float = -8.5,
    max_true_peak_dbfs: float = -0.5,
    tolerance_lu: float = 0.2,
) -> float:
    """Gain in dB, der den Track auf target_lufs bringt, auf 2 Nachkommastellen gerundet.

    - lufs None -> 0.0
    - gain = target_lufs - lufs
    - gain < 0 (Absenken): immer voll; true_peak_dbfs spielt keine Rolle (auch None ist ok).
    - gain > 0 (Anheben): true_peak_dbfs None -> 0.0; sonst höchstens
      max_true_peak_dbfs - true_peak_dbfs, nie unter 0. Kein Limiter, nie.
    - |gain| <= tolerance_lu (nach der Headroom-Begrenzung) -> 0.0
    Wirft nie.
    """
    try:
        if lufs is None:
            return 0.0
        gain = float(target_lufs) - float(lufs)
        if gain < 0:
            effective = gain
        elif gain > 0:
            if true_peak_dbfs is None:
                return 0.0
            headroom = float(max_true_peak_dbfs) - float(true_peak_dbfs)
            if headroom <= 0:
                return 0.0
            effective = min(gain, headroom)
        else:
            return 0.0

        if abs(effective) <= tolerance_lu:
            return 0.0

        return round(effective, 2)
    except Exception:
        return 0.0


def _tpdf_dither(shape: tuple[int, ...] | int) -> np.ndarray:
    """TPDF-Dither (Dreiecksverteilung im Bereich [-1.0, 1.0] LSB)."""
    return np.random.uniform(-0.5, 0.5, size=shape) + np.random.uniform(-0.5, 0.5, size=shape)


def _apply_gain_wav(raw: bytes, factor: float) -> bytes | None:
    """WAV-PCM: Nur Audiodaten im data-Chunk ersetzen, alle Chunks unverändert lassen."""
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return None

    pos = 12
    fmt_payload: bytes | None = None
    data_pos: int | None = None
    data_size: int | None = None

    while pos + 8 <= len(raw):
        cid = raw[pos : pos + 4]
        (csize,) = struct.unpack("<I", raw[pos + 4 : pos + 8])
        p_start = pos + 8
        if p_start + csize > len(raw):
            return None  # Unvollständiger Chunk
        if cid == b"fmt ":
            fmt_payload = raw[p_start : p_start + csize]
        elif cid == b"data":
            data_pos = p_start
            data_size = csize
        pos += 8 + csize + (csize % 2)

    if fmt_payload is None or data_pos is None or data_size is None or len(fmt_payload) < 16:
        return None

    wFormatTag, nChannels, nSamplesPerSec, nAvgBytesPerSec, nBlockAlign, wBitsPerSample = (
        struct.unpack("<HHIIHH", fmt_payload[:16])
    )

    if wFormatTag == 1:
        if wBitsPerSample not in (16, 24, 32):
            return None
    elif wFormatTag == 0xFFFE:  # WAVE_FORMAT_EXTENSIBLE
        if len(fmt_payload) < 40:
            return None
        cbSize = struct.unpack("<H", fmt_payload[16:18])[0]
        if cbSize < 22:
            return None
        subformat = fmt_payload[24:40]
        if subformat != SUBFORMAT_PCM or wBitsPerSample not in (16, 24, 32):
            return None
    else:
        return None

    bytes_per_sample = wBitsPerSample // 8
    expected_align = nChannels * bytes_per_sample
    if nBlockAlign != expected_align or nBlockAlign == 0:
        return None
    if data_size % nBlockAlign != 0:
        return None

    raw_audio = raw[data_pos : data_pos + data_size]

    if wBitsPerSample == 16:
        samples = np.frombuffer(raw_audio, dtype="<i2").astype(np.float64)
        scaled = samples * factor + _tpdf_dither(samples.shape)
        clipped = np.clip(np.round(scaled), -32768, 32767).astype("<i2")
        new_audio = clipped.tobytes()
    elif wBitsPerSample == 24:
        raw_u8 = np.frombuffer(raw_audio, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        i = raw_u8[:, 0] | (raw_u8[:, 1] << 8) | (raw_u8[:, 2] << 16)
        samples = np.where(i >= 1 << 23, i - (1 << 24), i).astype(np.float64)
        scaled = samples * factor + _tpdf_dither(samples.shape)
        clipped = np.clip(np.round(scaled), -8388608, 8388607).astype("<i4")
        new_audio = clipped.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    elif wBitsPerSample == 32:
        samples = np.frombuffer(raw_audio, dtype="<i4").astype(np.float64)
        scaled = samples * factor + _tpdf_dither(samples.shape)
        clipped = np.clip(np.round(scaled), -2147483648, 2147483647).astype("<i4")
        new_audio = clipped.tobytes()
    else:
        return None

    if len(new_audio) != data_size:
        return None

    return raw[:data_pos] + new_audio + raw[data_pos + data_size :]


def _apply_gain_aiff(raw: bytes, factor: float) -> bytes | None:
    """AIFF-PCM: Nur Audiodaten im SSND-Chunk ersetzen, alle Chunks unverändert lassen."""
    if len(raw) < 12 or raw[:4] != b"FORM" or raw[8:12] != b"AIFF":
        return None

    pos = 12
    comm_payload: bytes | None = None
    ssnd_pos: int | None = None
    ssnd_size: int | None = None

    while pos + 8 <= len(raw):
        cid = raw[pos : pos + 4]
        (csize,) = struct.unpack(">I", raw[pos + 4 : pos + 8])
        p_start = pos + 8
        if p_start + csize > len(raw):
            return None
        if cid == b"COMM":
            comm_payload = raw[p_start : p_start + csize]
        elif cid == b"SSND":
            ssnd_pos = p_start
            ssnd_size = csize
        pos += 8 + csize + (csize % 2)

    if comm_payload is None or ssnd_pos is None or ssnd_size is None:
        return None
    if len(comm_payload) < 8 or ssnd_size < 8:
        return None

    numChannels, numSampleFrames, sampleSize = struct.unpack(">hIh", comm_payload[:8])
    if sampleSize not in (16, 24, 32):
        return None

    offset, blockSize = struct.unpack(">II", raw[ssnd_pos : ssnd_pos + 8])
    sample_start = ssnd_pos + 8 + offset
    sample_size = ssnd_size - 8 - offset

    if sample_start + sample_size > ssnd_pos + ssnd_size or sample_size < 0:
        return None

    bytes_per_sample = sampleSize // 8
    block_align = numChannels * bytes_per_sample
    if block_align == 0 or sample_size % block_align != 0:
        return None

    raw_audio = raw[sample_start : sample_start + sample_size]

    if sampleSize == 16:
        samples = np.frombuffer(raw_audio, dtype=">i2").astype(np.float64)
        scaled = samples * factor + _tpdf_dither(samples.shape)
        clipped = np.clip(np.round(scaled), -32768, 32767).astype(">i2")
        new_audio = clipped.tobytes()
    elif sampleSize == 24:
        raw_u8 = np.frombuffer(raw_audio, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        i = (raw_u8[:, 0] << 16) | (raw_u8[:, 1] << 8) | raw_u8[:, 2]
        samples = np.where(i >= 1 << 23, i - (1 << 24), i).astype(np.float64)
        scaled = samples * factor + _tpdf_dither(samples.shape)
        clipped = np.clip(np.round(scaled), -8388608, 8388607).astype(">i4")
        new_audio = clipped.view(np.uint8).reshape(-1, 4)[:, 1:4].tobytes()
    elif sampleSize == 32:
        samples = np.frombuffer(raw_audio, dtype=">i4").astype(np.float64)
        scaled = samples * factor + _tpdf_dither(samples.shape)
        clipped = np.clip(np.round(scaled), -2147483648, 2147483647).astype(">i4")
        new_audio = clipped.tobytes()
    else:
        return None

    if len(new_audio) != sample_size:
        return None

    return raw[:sample_start] + new_audio + raw[sample_start + sample_size :]


def _parse_flac_blocks(data: bytes) -> tuple[list[tuple[int, bytes]], int]:
    """Liest die Metadaten-Blöcke einer FLAC-Datei ein."""
    if len(data) < 4 or data[:4] != b"fLaC":
        raise ValueError("Kein gültiger FLAC-Header")
    blocks: list[tuple[int, bytes]] = []
    pos = 4
    last = False
    while not last:
        if pos + 4 > len(data):
            raise ValueError("FLAC-Metadaten unvollständig")
        hdr = data[pos]
        last = bool(hdr & 0x80)
        btype = hdr & 0x7F
        size = int.from_bytes(data[pos + 1 : pos + 4], "big")
        pos += 4
        if pos + size > len(data):
            raise ValueError("FLAC-Block unvollständig")
        blocks.append((btype, data[pos : pos + size]))
        pos += size
    return blocks, pos


def _apply_gain_flac(path: Path, raw: bytes, factor: float, temp_files: list[Path]) -> bytes | None:
    """FLAC: Audio neu kodieren, STREAMINFO aktualisieren, übrige Metadaten-Blöcke byte-identisch übernehmen."""
    if len(raw) < 4 or raw[:4] != b"fLaC":
        return None

    info = sf.info(str(path))
    if info.format != "FLAC" or info.subtype not in ("PCM_16", "PCM_24"):
        return None

    orig_blocks, _ = _parse_flac_blocks(raw)
    if not orig_blocks or orig_blocks[0][0] != 0:
        return None

    enc_tmp = path.with_name(f".{path.name}.enc.{uuid.uuid4().hex}.flac")
    temp_files.append(enc_tmp)

    if info.subtype == "PCM_16":
        samples, sr = sf.read(str(path), dtype="int16")
        f_samples = samples.astype(np.float64)
        scaled = f_samples * factor + _tpdf_dither(f_samples.shape)
        clipped = np.clip(np.round(scaled), -32768, 32767).astype(np.int16)
        sf.write(str(enc_tmp), clipped, sr, subtype="PCM_16", format="FLAC")
    elif info.subtype == "PCM_24":
        samples_32, sr = sf.read(str(path), dtype="int32")
        samples_24 = samples_32 >> 8
        f_samples = samples_24.astype(np.float64)
        scaled = f_samples * factor + _tpdf_dither(f_samples.shape)
        clipped = np.clip(np.round(scaled), -8388608, 8388607).astype(np.int32)
        to_write = clipped << 8
        sf.write(str(enc_tmp), to_write, sr, subtype="PCM_24", format="FLAC")
    else:
        return None

    enc_bytes = enc_tmp.read_bytes()
    enc_blocks, enc_audio_start = _parse_flac_blocks(enc_bytes)
    enc_streaminfo = enc_blocks[0][1]

    # STREAMINFO (0) durch Encoder-Block ersetzen; SEEKTABLE (3) verwerfen; alle anderen erhalten
    final_blocks = [(0, enc_streaminfo)] + [b for b in orig_blocks if b[0] not in (0, 3)]

    parts = [b"fLaC"]
    for i, (btype, bdata) in enumerate(final_blocks):
        is_last = (i == len(final_blocks) - 1)
        hdr = (0x80 if is_last else 0x00) | (btype & 0x7F)
        parts.append(bytes([hdr]) + len(bdata).to_bytes(3, "big") + bdata)
    parts.append(enc_bytes[enc_audio_start:])
    return b"".join(parts)


def apply_gain(path: Path, gain_db: float) -> bool:
    """Skaliert die Audiodaten von path in place um gain_db. True, wenn die Datei geändert wurde.

    Unterstützt: WAV (PCM 16/24/32 Bit, auch WAVE_FORMAT_EXTENSIBLE mit PCM-Subformat),
    AIFF (PCM 16/24/32 Bit; kein AIFC), FLAC (16/24 Bit).
    Alles andere (MP3, M4A, Float-WAV, AIFC, 8 Bit, kaputte Dateien …) -> False, Datei unverändert.
    gain_db == 0.0 -> False, Datei unverändert.

    Pflicht:
    - Samplerate, Bittiefe, Kanalzahl und Sample-Anzahl bleiben gleich, kein Versatz
      (Rekordbox/Engine speichern Cues und Beatgrids als Position in ihrer Datenbank).
    - WAV/AIFF: nur die Samples im data- bzw. SSND-Chunk werden ersetzt (gleiche Länge).
      Alle anderen Chunks, Pad-Bytes, die Chunk-Reihenfolge, der SSND-Kopf (offset/blockSize)
      und die Dateigröße bleiben byte-identisch. Kein Neu-Rendern in einen neuen Container
      (ffmpeg und soundfile verwerfen unbekannte Chunks).
    - FLAC: Audio neu kodieren. STREAMINFO des Encoders (neue MD5, gleiche Werte sonst) ist der
      erste Block; alle übrigen Metadaten-Blöcke der Originaldatei außer SEEKTABLE (Byte-Offsets
      stimmen nicht mehr) werden byte-identisch und in der Originalreihenfolge übernommen
      (VORBIS_COMMENT, PICTURE, APPLICATION, CUESHEET, PADDING). Letzter Block trägt das Last-Flag.
    - Rundung mit TPDF-Dither (±1 LSB); bei Übersteuerung sättigen statt umklappen.
    - Atomar: Temp-Datei im selben Ordner schreiben, dann os.replace(). Bei jedem Fehler:
      log.warning (mit „Normalisierung“ im Text), Temp-Datei entfernen, Original unverändert,
      False. Wirft nie.
    """
    if gain_db == 0.0:
        return False
    if not isinstance(path, Path):
        path = Path(path)
    if not path.is_file():
        return False

    temp_files: list[Path] = []
    try:
        raw = path.read_bytes()
        if len(raw) < 12:
            return False

        factor = 10.0 ** (gain_db / 20.0)
        new_data: bytes | None = None

        if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
            new_data = _apply_gain_wav(raw, factor)
        elif raw[:4] == b"FORM" and raw[8:12] == b"AIFF":
            new_data = _apply_gain_aiff(raw, factor)
        elif raw[:4] == b"fLaC":
            new_data = _apply_gain_flac(path, raw, factor, temp_files)
        else:
            return False

        if new_data is None:
            return False

        temp_out = path.with_name(f".{path.name}.tmp.{uuid.uuid4().hex}")
        temp_files.append(temp_out)
        temp_out.write_bytes(new_data)
        os.replace(temp_out, path)
        temp_files.remove(temp_out)
        return True
    except Exception as e:
        log.warning("Fehler bei der Normalisierung von %s: %s", path, e)
        return False
    finally:
        for tmp in temp_files:
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass


def normalize_inbox_file(path: Path, report: dict | None, cfg: Config) -> float:
    """Normalisiert eine frisch geladene Inbox-Datei nach cfg.raw["loudness"] und aktualisiert report.

    - Abschnitt "loudness" fehlt, normalize_inbox falsch oder report kein dict -> 0.0, nichts passiert.
    - gain = plan_gain(report.get("integrated_lufs"), report.get("true_peak_dbfs"),
                       target_lufs=<target_lufs, Default -8.5>,
                       max_true_peak_dbfs=<max_true_peak_dbfs, Default -0.5>)
    - gain == 0.0 oder apply_gain(path, gain) False -> 0.0, report unverändert.
    - Sonst report in place: integrated_lufs += gain; true_peak_dbfs += gain (falls nicht None);
      loudness_range_lu unverändert; neuer Schlüssel "gain_applied_db" = gain. Rückgabe gain.
      (Keine Neumessung: lineare Verstärkung verschiebt LUFS und True Peak exakt um gain.)
    Wirft nie.
    """
    try:
        if not isinstance(report, dict):
            return 0.0
        loudness_cfg = (
            cfg.raw.get("loudness")
            if (cfg and hasattr(cfg, "raw") and isinstance(cfg.raw, dict))
            else None
        )
        if not isinstance(loudness_cfg, dict) or not loudness_cfg.get("normalize_inbox", False):
            return 0.0

        target_lufs = float(loudness_cfg.get("target_lufs", -8.5))
        max_true_peak_dbfs = float(loudness_cfg.get("max_true_peak_dbfs", -0.5))

        gain = plan_gain(
            report.get("integrated_lufs"),
            report.get("true_peak_dbfs"),
            target_lufs=target_lufs,
            max_true_peak_dbfs=max_true_peak_dbfs,
        )
        if gain == 0.0:
            return 0.0

        if not apply_gain(path, gain):
            return 0.0

        if report.get("integrated_lufs") is not None:
            report["integrated_lufs"] = round(report["integrated_lufs"] + gain, 2)
        if report.get("true_peak_dbfs") is not None:
            report["true_peak_dbfs"] = round(report["true_peak_dbfs"] + gain, 2)
        report["gain_applied_db"] = gain
        return gain
    except Exception as e:
        log.warning("Fehler bei der Normalisierung von %s: %s", path, e)
        return 0.0
