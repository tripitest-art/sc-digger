"""Audio-Qualitätsprüfung: Container/Bitrate per ffprobe, Fake-Erkennung per Spektrum.

Bitrate allein täuscht: Ein 128-kbps-MP3, das nach 320 kbps oder WAV konvertiert wurde,
hat trotzdem eine harte Frequenzgrenze bei ~16 kHz. Das lässt sich im Spektrum sehen.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

import numpy as np

from .models import Config

log = logging.getLogger(__name__)


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def probe(path: Path) -> dict:
    r = _run([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,sample_rate,channels,bit_rate,bits_per_raw_sample",
        "-show_entries", "format=format_name,duration,bit_rate",
        "-of", "json", str(path),
    ])
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace"))
    return json.loads(r.stdout)


def spectral_cutoff_hz(path: Path, seconds: int = 30, offset: int = 60) -> float:
    """Schätzt die obere Frequenzgrenze des Materials.

    Dekodiert einen Ausschnitt aus dem Trackinneren (offset), mittelt das Spektrum
    und sucht die höchste Frequenz, die noch deutlich über dem Rauschboden liegt.
    """
    r = _run([
        "ffmpeg", "-v", "error", "-ss", str(offset), "-t", str(seconds),
        "-i", str(path), "-ac", "1", "-ar", "44100", "-f", "f32le", "-",
    ], timeout=180)
    audio = np.frombuffer(r.stdout, dtype=np.float32)
    if audio.size < 44100 * 5:
        # Track kürzer als offset -> vom Anfang versuchen
        r = _run([
            "ffmpeg", "-v", "error", "-t", str(seconds),
            "-i", str(path), "-ac", "1", "-ar", "44100", "-f", "f32le", "-",
        ], timeout=180)
        audio = np.frombuffer(r.stdout, dtype=np.float32)
    if audio.size < 44100 * 3:
        return 0.0

    n_fft = 4096
    hop = n_fft // 2
    window = np.hanning(n_fft)
    frames = [
        np.abs(np.fft.rfft(audio[i:i + n_fft] * window))
        for i in range(0, audio.size - n_fft, hop)
    ]
    spec = 20 * np.log10(np.mean(frames, axis=0) + 1e-12)
    freqs = np.fft.rfftfreq(n_fft, 1 / 44100)

    # Referenzpegel: Median im Musikband 1-8 kHz. Cutoff = letzte Frequenz,
    # die noch innerhalb von 60 dB unter dieser Referenz liegt.
    ref = np.median(spec[(freqs > 1000) & (freqs < 8000)])
    active = np.where(spec > ref - 60)[0]
    return float(freqs[active[-1]]) if active.size else 0.0


def check_file(path: Path, cfg: Config) -> dict:
    """Gibt einen Report zurück: {ok, format, bitrate_kbps, cutoff_hz, reason}."""
    q = cfg["quality"]
    info = probe(path)
    stream = (info.get("streams") or [{}])[0]
    fmt = info.get("format", {})
    codec = stream.get("codec_name", "")
    ext = path.suffix.lower().lstrip(".")
    bitrate = int(stream.get("bit_rate") or fmt.get("bit_rate") or 0) // 1000
    sample_rate = int(stream.get("sample_rate") or 0)

    report = {
        "file": path.name, "codec": codec, "ext": ext,
        "bitrate_kbps": bitrate, "sample_rate": sample_rate,
        "cutoff_hz": None, "ok": False, "reason": "",
    }

    is_lossless = ext in q["accepted_lossless"] or codec in ("flac", "pcm_s16le", "pcm_s24le", "alac")
    is_mp3 = codec == "mp3"

    if is_mp3 and bitrate < q["min_mp3_bitrate_kbps"]:
        report["reason"] = f"MP3 mit nur {bitrate} kbps"
        return report
    if not is_lossless and not is_mp3:
        report["reason"] = f"Codec {codec} nicht akzeptiert (z. B. AAC/Opus-Rip)"
        return report

    cutoff = spectral_cutoff_hz(path)
    report["cutoff_hz"] = round(cutoff)
    limit = q["min_cutoff_hz_for_lossless"] if is_lossless else q["min_cutoff_hz_for_320"]
    if cutoff and cutoff < limit:
        kind = "Lossless-Container" if is_lossless else "320er MP3"
        report["reason"] = (
            f"{kind}, aber Spektrum endet bei {cutoff / 1000:.1f} kHz "
            f"(vermutlich hochkonvertierte Lossy-Quelle)"
        )
        return report

    report["ok"] = True
    report["reason"] = "echte Qualität"
    return report
