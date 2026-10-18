"""Akzeptanztests: 32-Bit Float/Int WAV nach 24-Bit PCM Konvertierung für Rekordbox (Issue #365)."""
import subprocess
from pathlib import Path
import pytest

from sc_digger.models import Config, Track
from sc_digger.output import finalize_quality
from sc_digger.quality import convert_to_pcm24, is_32bit_or_float, probe

CFG = Config.load(Path(__file__).resolve().parents[2] / "config.yaml")


def _make_float32_wav(path: Path) -> Path:
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "aevalsrc=0.3*sin(2*PI*440*t)*t/4:s=44100:d=4", "-ac", "2", "-ar", "44100",
        "-c:a", "pcm_f32le", str(path),
    ]
    subprocess.run(cmd, check=True)
    return path


def _make_track(track_id: int = 12345) -> Track:
    return Track(
        id=track_id, title="Float Test Track", url="https://soundcloud.com/test",
        artist="Artist", artist_url="", created_at="", duration_ms=4000,
        genre="Schranz", tags=[], description="", bpm=155.0, plays=1000,
        likes=100, reposts=10, comments=5, downloadable=True,
        has_downloads_left=True, purchase_url=None, purchase_title=None,
    )


def test_is_32bit_or_float_detects_float_and_pcm32(tmp_path: Path):
    f32_path = _make_float32_wav(tmp_path / "test_float.wav")
    assert is_32bit_or_float(f32_path) is True

    # 16-Bit WAV
    p16_path = tmp_path / "test_16bit.wav"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "anoisesrc=c=white:a=0.3:s=1:d=2", "-c:a", "pcm_s16le", str(p16_path),
    ], check=True)
    assert is_32bit_or_float(p16_path) is False


def test_convert_to_pcm24_converts_float32_to_pcm24le(tmp_path: Path):
    f32_path = _make_float32_wav(tmp_path / "convert_float.wav")
    ok = convert_to_pcm24(f32_path)
    assert ok is True

    # Nach Konvertierung muss ffprobe pcm_s24le melden
    info = probe(f32_path)
    stream = info["streams"][0]
    assert stream["codec_name"] == "pcm_s24le"
    assert is_32bit_or_float(f32_path) is False


def test_finalize_quality_automatically_converts_float32_wav(tmp_path: Path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    dl_path = inbox / "float_track.wav"
    _make_float32_wav(dl_path)

    t = _make_track()
    out = finalize_quality(t, dl_path, inbox, CFG)

    assert out is not None
    assert out.exists()
    assert "_rejected" not in str(out)

    info = probe(out)
    assert info["streams"][0]["codec_name"] == "pcm_s24le"
    assert any("24-Bit" in note for note in t.notes)
