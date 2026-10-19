"""Eigene Tests zur 24-Bit-PCM-Konvertierung (Issue #365), ergänzend zu den Akzeptanztests.

Randfälle: AIFF (Big Endian), bereits 24 Bit, schreibgeschützte Datei, kaputte/fehlende
Datei, komprimierte Formate und das rekursive Wartungsskript. Synthetisches Audio per
ffmpeg, kein Netzwerk.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from sc_digger.quality import convert_to_pcm24, is_32bit_or_float, probe
from scripts.fix_32bit_wavs import fix_directory


def _make_audio(path: Path, codec: str = "pcm_f32le", seconds: int = 1) -> Path:
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        f"sine=frequency=440:sample_rate=44100:duration={seconds}",
        "-c:a", codec, str(path),
    ], check=True)
    return path


def _codec(path: Path) -> str:
    return probe(path)["streams"][0]["codec_name"]


def test_float_aiff_wird_zu_pcm24be(tmp_path: Path):
    path = _make_audio(tmp_path / "float.aiff", codec="pcm_f32be")
    assert is_32bit_or_float(path) is True

    assert convert_to_pcm24(path) is True

    assert _codec(path) == "pcm_s24be"
    assert is_32bit_or_float(path) is False


def test_bereits_24bit_bleibt_unveraendert_und_ist_kein_32bit(tmp_path: Path):
    path = _make_audio(tmp_path / "already24.wav", codec="pcm_s24le")
    assert is_32bit_or_float(path) is False

    # Idempotent: erneute Konvertierung ist erlaubt, ändert das Format aber nicht.
    assert convert_to_pcm24(path) is True
    assert _codec(path) == "pcm_s24le"


def test_schreibgeschuetzte_datei_wird_konvertiert_und_bleibt_schreibgeschuetzt(tmp_path: Path):
    path = _make_audio(tmp_path / "readonly.wav")
    os.chmod(path, 0o444)
    try:
        assert convert_to_pcm24(path) is True

        assert _codec(path) == "pcm_s24le"
        assert path.stat().st_mode & 0o777 == 0o444
    finally:
        os.chmod(path, 0o644)


def test_kaputte_datei_gibt_false_und_bleibt_erhalten(tmp_path: Path):
    path = tmp_path / "kaputt.wav"
    path.write_bytes(b"kein audio")

    assert is_32bit_or_float(path) is False
    assert convert_to_pcm24(path) is False
    assert path.read_bytes() == b"kein audio"


def test_fehlende_datei_gibt_false(tmp_path: Path):
    assert is_32bit_or_float(tmp_path / "fehlt.wav") is False
    assert convert_to_pcm24(tmp_path / "fehlt.wav") is False


def test_komprimierte_formate_werden_nicht_konvertiert(tmp_path: Path):
    # Float-Endung vortäuschen: .flac mit WAV-Inhalt darf nicht angefasst werden.
    quelle = _make_audio(tmp_path / "quelle.wav")
    path = tmp_path / "track.flac"
    path.write_bytes(quelle.read_bytes())

    assert convert_to_pcm24(path) is False
    assert path.read_bytes() == quelle.read_bytes()


def test_konvertierung_laesst_keine_temp_datei_zurueck(tmp_path: Path):
    path = _make_audio(tmp_path / "temp.wav")
    assert convert_to_pcm24(path) is True

    assert [p.name for p in tmp_path.iterdir()] == ["temp.wav"]


def test_skript_konvertiert_rekursiv_und_dry_run_aendert_nichts(tmp_path: Path):
    tief = tmp_path / "2026" / "08"
    tief.mkdir(parents=True)
    float_datei = _make_audio(tief / "float.wav")
    _make_audio(tmp_path / "ok16.wav", codec="pcm_s16le")

    assert fix_directory(tmp_path, dry_run=True) == (2, 1, 0)
    assert _codec(float_datei) == "pcm_f32le"

    assert fix_directory(tmp_path, dry_run=False) == (2, 1, 0)
    assert _codec(float_datei) == "pcm_s24le"
    assert is_32bit_or_float(float_datei) is False
