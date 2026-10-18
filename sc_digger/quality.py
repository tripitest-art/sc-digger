"""Audio-Qualitätsprüfung: Container/Bitrate per ffprobe, Fake-Erkennung per Spektrum,
Lautheit/Clipping per EBU R128.

Bitrate allein täuscht: Ein 128-kbps-MP3, das nach 320 kbps oder WAV konvertiert wurde,
hat trotzdem eine harte Frequenzgrenze bei ~16 kHz. Das lässt sich im Spektrum sehen.

Separat davon: viele Hard-Techno-Free-DLs sind brickwall-gemastert (kein Headroom mehr,
Dauer-Clipping). Das ist ein Mastering-Problem, kein Fake-Problem, deshalb ein eigenes
Flag ("clipped") statt es mit "ok" zu vermischen.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

import numpy as np

from .models import Config

log = logging.getLogger(__name__)

_LUFS_RE = re.compile(r"^\s*I:\s*(-?\d+\.?\d*)\s*LUFS", re.M)
_PEAK_RE = re.compile(r"^\s*Peak:\s*(-?\d+\.?\d*)\s*dBFS", re.M)
_LRA_RE = re.compile(r"^\s*LRA:\s*(-?\d+\.?\d*)\s*LU\b", re.M)


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


# Abtastrate, auf die für die Spektrumsmessung dekodiert wird (Nyquist = 22,05 kHz).
_SR = 44100
# Maximalamplitude (Vollaussteuerung = 1.0), unter der nichts Messbares mehr liegt (≈ -80 dBFS).
# Ein 16-Bit-Rauschen von ±1 LSB (≈ 3e-5) fällt ebenfalls darunter. Stille würde sonst
# im Spektrum nur Rauschboden vergleichen und fälschlich "volle Bandbreite" melden.
_SILENCE_PEAK = 1e-4
# Ab dieser Dauer misst check_file an zwei Stellen statt an einer (siehe _cutoff_offsets).
_MULTI_WINDOW_MIN_S = 150

# Verlustfreie Codecs neben allen PCM-Varianten (pcm_s16le, pcm_s24le, pcm_s32le, pcm_f32le,
# pcm_s16be, …). Die Dateiendung allein sagt nichts: ein AAC-Stream kann in .wav stecken.
_LOSSLESS_CODECS = ("flac", "alac", "wavpack", "ape", "tta")


def is_lossless_codec(codec: str) -> bool:
    """True für verlustfreie Codecs: alle PCM-Formate sowie FLAC, ALAC, WavPack, APE, TTA."""
    codec = codec or ""
    return codec.startswith("pcm_") or codec in _LOSSLESS_CODECS


def _decode_window(path: Path, seconds: int, offset: int | None) -> np.ndarray:
    """Dekodiert einen Ausschnitt als Mono-float32 bei 44,1 kHz. offset=None: ab Dateianfang."""
    cmd = ["ffmpeg", "-v", "error"]
    if offset is not None:
        cmd += ["-ss", str(offset)]
    cmd += ["-t", str(seconds), "-i", str(path), "-ac", "1", "-ar", str(_SR), "-f", "f32le", "-"]
    return np.frombuffer(_run(cmd, timeout=180).stdout, dtype=np.float32)


def _window_cutoff_hz(audio: np.ndarray) -> float:
    """Obere Frequenzgrenze eines dekodierten Ausschnitts; 0.0 bei Stille/nicht messbar."""
    if not float(np.max(np.abs(audio))) >= _SILENCE_PEAK:   # fängt auch NaN ab
        return 0.0

    n_fft = 4096
    hop = n_fft // 2
    window = np.hanning(n_fft)
    frames = [
        np.abs(np.fft.rfft(audio[i:i + n_fft] * window))
        for i in range(0, audio.size - n_fft, hop)
    ]
    spec = 20 * np.log10(np.mean(frames, axis=0) + 1e-12)
    freqs = np.fft.rfftfreq(n_fft, 1 / _SR)

    # Referenzpegel: Median im Musikband 1-8 kHz. Cutoff = letzte Frequenz,
    # die noch innerhalb von 60 dB unter dieser Referenz liegt.
    ref = np.median(spec[(freqs > 1000) & (freqs < 8000)])
    active = np.where(spec > ref - 60)[0]
    return float(freqs[active[-1]]) if active.size else 0.0


def spectral_cutoff_hz(
    path: Path,
    seconds: int = 30,
    offset: int = 60,
    offsets: tuple[int, ...] | list[int] | None = None,
) -> float:
    """Schätzt die obere Frequenzgrenze des Materials.

    Dekodiert einen Ausschnitt aus dem Trackinneren (offset), mittelt das Spektrum
    und sucht die höchste Frequenz, die noch deutlich über dem Rauschboden liegt.

    Wird `offsets` übergeben (z. B. (30, 75)), wird an allen angegebenen Offsets gemessen
    und das Maximum zurückgegeben. Das schützt vor langen, leisen Intros: Ein Fake ist
    überall abgeschnitten, ein echter Track zeigt seine Bandbreite spätestens im lauten Teil.
    Liegt keine Messung über 0.0, wird 0.0 zurückgegeben. Reine Stille liefert 0.0.

    Ein Fenster, das hinter dem Dateiende liegt (zu wenig Audio), zählt nicht. Hat gar kein
    Fenster genug Audio, wird vom Dateianfang gemessen (kurze Dateien).
    """
    # Mindestlänge je Fenster: 5 s bzw. 3 s ab Dateianfang; bei kurzen Fenstern (seconds < 5)
    # entsprechend weniger, sonst könnte ein 2-s-Fenster nie als gültig durchgehen.
    min_window = int(min(5.0, 0.9 * seconds) * _SR)
    min_start = int(min(3.0, 0.9 * seconds) * _SR)

    windows = list(dict.fromkeys(max(0, int(o)) for o in offsets)) if offsets else [offset]
    cutoffs: list[float] = []
    for off in windows:
        audio = _decode_window(path, seconds, off)
        if audio.size >= min_window:
            cutoffs.append(_window_cutoff_hz(audio))
    if not cutoffs:
        # Track kürzer als alle Offsets -> vom Anfang versuchen
        audio = _decode_window(path, seconds, None)
        if audio.size >= min_start:
            cutoffs.append(_window_cutoff_hz(audio))
    return max(cutoffs, default=0.0)


def _cutoff_offsets(duration_s: float, seconds: int = 30) -> tuple[int, ...] | None:
    """Messfenster für check_file: zwei ab _MULTI_WINDOW_MIN_S, sonst None (ein Standardfenster).

    Erstes Fenster bei 60 s wie bisher, zweites bei 60 % der Dauer (mindestens direkt hinter
    dem ersten, höchstens so, dass es noch vollständig in den Track passt). Bei 5 Minuten
    also (60, 180).
    """
    if duration_s < _MULTI_WINDOW_MIN_S:
        return None
    first = 60
    second = max(first + seconds, min(int(duration_s * 0.6), int(duration_s) - seconds))
    return (first, second)


def loudness_report(path: Path) -> dict:
    """Integrierte Lautheit (LUFS), Loudness Range (LRA) und True Peak per ffmpeg-ebur128.

    Kalibrierung anhand einer Stichprobe aus Stephans eigener Sammlung (2026-09-19):
    Hard-Techno/Schranz-Master liegen dort routinemäßig bei -3.9 bis -6.8 LUFS integriert
    und 0 bis +2.0 dBFS True Peak (Inter-Sample-Peaks über 0 dBFS sind im Genre normal,
    kein Fake- oder Fehlersignal). LUFS und True Peak allein taugen deshalb nicht als
    Clipping-Filter. LRA (Loudness Range) dagegen war in derselben Stichprobe durchgehend
    ≥ 3.4 LU bei akzeptierten Tracks -> ein wirklich brickwall-gemastertes "Dauer-Clipping"
    ohne jede Dynamik zeigt sich zuverlässiger als sehr niedrige LRA, nicht als hohe Lautheit.
    """
    r = _run([
        "ffmpeg", "-v", "info", "-i", str(path),
        "-filter:a", "ebur128=peak=true", "-f", "null", "-",
    ], timeout=180)
    stderr = r.stderr.decode(errors="replace")
    i_matches = _LUFS_RE.findall(stderr)
    p_matches = _PEAK_RE.findall(stderr)
    lra_matches = _LRA_RE.findall(stderr)
    return {
        "integrated_lufs": float(i_matches[-1]) if i_matches else None,
        "true_peak_dbfs": float(p_matches[-1]) if p_matches else None,
        "loudness_range_lu": float(lra_matches[-1]) if lra_matches else None,
    }


def is_vbr_mp3(path: Path) -> bool:
    """True, wenn im ersten MP3-Frame ein Xing- oder VBRI-Kopf steht.

    Beginnt die Datei mit "ID3": Tag überspringen (Größe syncsafe aus Byte 6–9, plus 10 Byte Kopf,
    plus 10 Byte Footer, wenn Flag 0x10 in Byte 5 gesetzt). Ab dort 4096 Byte lesen und
    nach b"Xing" oder b"VBRI" suchen. Jeder Fehler (Datei fehlt, zu kurz, …) -> False, wirft nie.
    """
    try:
        path = Path(path)
        with open(path, "rb") as f:
            header = f.read(10)
            if len(header) < 10:
                return False
            offset = 0
            if header[:3] == b"ID3":
                flags = header[5]
                has_footer = bool(flags & 0x10)
                tag_size = (
                    ((header[6] & 0x7F) << 21)
                    | ((header[7] & 0x7F) << 14)
                    | ((header[8] & 0x7F) << 7)
                    | (header[9] & 0x7F)
                )
                offset = 10 + tag_size + (10 if has_footer else 0)
            f.seek(offset)
            chunk = f.read(4096)
            return (b"Xing" in chunk) or (b"VBRI" in chunk)
    except Exception:
        return False


def check_file(path: Path, cfg: Config) -> dict:
    """Gibt einen Report zurück: {ok, format, bitrate_kbps, cutoff_hz, reason, ...}.

    - Lossless gilt nur, wenn die Endung in accepted_lossless steht UND der Codec verlustfrei
      ist (is_lossless_codec: alle pcm_*, flac, alac, wavpack, ape, tta). Ein Lossy-Stream
      (z. B. AAC in .wav) wird abgewiesen, ein verlustfreier Codec hinter einer nicht
      akzeptierten Endung ebenfalls.
    - cutoff <= 0.0 -> ok=False, reason="Spektrum nicht messbar (Stille oder kein Signal)"
    - cutoff < limit -> ok=False, reason="{kind}, aber Spektrum endet bei ... kHz (...)"
    - cutoff >= limit -> ok=True, reason="echte Qualität"
    Bei Dateien ab _MULTI_WINDOW_MIN_S wird an zwei Stellen gemessen (Maximum zählt).
    """
    q = cfg["quality"]
    info = probe(path)
    stream = (info.get("streams") or [{}])[0]
    fmt = info.get("format", {})
    codec = stream.get("codec_name", "")
    ext = path.suffix.lower().lstrip(".")
    bitrate = int(stream.get("bit_rate") or fmt.get("bit_rate") or 0) // 1000
    sample_rate = int(stream.get("sample_rate") or 0)
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):   # ffprobe liefert bei manchen Containern "N/A"
        duration = 0.0

    report = {
        "file": path.name, "codec": codec, "ext": ext,
        "bitrate_kbps": bitrate, "sample_rate": sample_rate,
        "cutoff_hz": None, "ok": False, "reason": "",
        "duration": duration if duration > 0 else None,
    }

    ext_lossless = ext in q["accepted_lossless"]
    codec_lossless = is_lossless_codec(codec)
    is_lossless = ext_lossless and codec_lossless
    is_mp3 = codec == "mp3"
    is_vbr = is_mp3 and is_vbr_mp3(path)

    if ext_lossless and not codec_lossless:
        # Endung verspricht Lossless, im Container steckt aber Lossy (z. B. AAC/MP3 in .wav)
        report["reason"] = f"Codec {codec} nicht akzeptiert (Lossy-Stream in .{ext}-Container)"
        return report
    if is_mp3 and not is_vbr and bitrate < q["min_mp3_bitrate_kbps"]:
        report["reason"] = f"MP3 mit nur {bitrate} kbps"
        return report
    if not is_lossless and not is_mp3:
        if codec_lossless:
            report["reason"] = (
                f"Codec {codec} nicht akzeptiert (Endung .{ext} steht nicht in quality.accepted_lossless)"
            )
        else:
            report["reason"] = f"Codec {codec} nicht akzeptiert (z. B. AAC/Opus-Rip)"
        return report

    cutoff = spectral_cutoff_hz(path, offsets=_cutoff_offsets(duration))
    report["cutoff_hz"] = round(cutoff)
    if cutoff <= 0.0:
        # Stille, ultrakurzer Ausschnitt oder nicht messbar: ohne Messung keine Freigabe.
        report["reason"] = "Spektrum nicht messbar (Stille oder kein Signal)"
        return report
    limit = q["min_cutoff_hz_for_lossless"] if is_lossless else q["min_cutoff_hz_for_320"]
    if cutoff < limit:
        if is_lossless:
            kind = "Lossless-Container"
        elif is_vbr:
            kind = "VBR-MP3"
        else:
            kind = "320er MP3"
        report["reason"] = (
            f"{kind}, aber Spektrum endet bei {cutoff / 1000:.1f} kHz "
            f"(vermutlich hochkonvertierte Lossy-Quelle)"
        )
        return report

    report["ok"] = True
    report["reason"] = "echte Qualität"

    loud = loudness_report(path)
    report["integrated_lufs"] = loud["integrated_lufs"]
    report["true_peak_dbfs"] = loud["true_peak_dbfs"]
    report["loudness_range_lu"] = loud["loudness_range_lu"]
    min_lra = q.get("min_loudness_range_lu", 2.5)
    max_tp = q.get("max_true_peak_dbfs", 3.0)
    report["clipped"] = bool(
        (loud["loudness_range_lu"] is not None and loud["loudness_range_lu"] < min_lra)
        or (loud["true_peak_dbfs"] is not None and loud["true_peak_dbfs"] >= max_tp)
    )
    return report
