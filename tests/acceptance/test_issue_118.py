"""Akzeptanztests: Live-Preview - Audio-Snippet-Extraktion per ffmpeg (Teil 1 von #53)."""
import subprocess
from pathlib import Path

import pytest

from sc_digger.preview import extract_preview, find_loudest_segment

ROOT = Path(__file__).resolve().parents[2]


def _make_audio_with_loud_burst(path: Path) -> Path:
    filter_graph = (
        "anoisesrc=d=5:c=pink:r=22050:a=0.01[s1];"
        "sine=f=440:d=10:r=22050[s2];"
        "anoisesrc=d=5:c=pink:r=22050:a=0.01[s3];"
        "[s1][s2][s3]concat=n=3:v=0:a=1[out]"
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-filter_complex", filter_graph,
            "-map", "[out]",
            str(path),
        ],
        check=True,
    )
    return path


def test_find_loudest_segment_detects_loud_section(tmp_path):
    wav = _make_audio_with_loud_burst(tmp_path / "burst.wav")
    start = find_loudest_segment(wav, segment_duration_s=5.0)
    assert 4.0 <= start <= 10.0


def test_find_loudest_segment_handles_missing_and_short_files(tmp_path):
    assert find_loudest_segment(tmp_path / "nonexistent.wav", 10.0) == 0.0

    short_wav = tmp_path / "short.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=2:r=22050", str(short_wav)],
        check=True,
    )
    assert find_loudest_segment(short_wav, segment_duration_s=5.0) == 0.0


def test_extract_preview_creates_valid_opus_file(tmp_path):
    wav = _make_audio_with_loud_burst(tmp_path / "source.wav")
    out_ogg = tmp_path / "preview.ogg"

    ok = extract_preview(wav, out_ogg, start_s=5.0, duration_s=5.0, bitrate_kbps=64)
    assert ok is True
    assert out_ogg.is_file()
    assert out_ogg.stat().st_size > 500

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=format_name", "-of", "default=noprint_wrappers=1:nokey=1", str(out_ogg)],
        capture_output=True,
        text=True,
    )
    assert "ogg" in probe.stdout.lower()


def test_extract_preview_default_start_uses_loudest_segment(tmp_path):
    wav = _make_audio_with_loud_burst(tmp_path / "source.wav")
    out_ogg = tmp_path / "auto_preview.ogg"

    ok = extract_preview(wav, out_ogg, start_s=None, duration_s=5.0)
    assert ok is True
    assert out_ogg.is_file()
    assert out_ogg.stat().st_size > 500


def test_extract_preview_handles_error_gracefully(tmp_path):
    out_ogg = tmp_path / "fail.ogg"
    ok = extract_preview(tmp_path / "nonexistent.wav", out_ogg)
    assert ok is False
    assert not out_ogg.exists()


def test_entwicklung_md_contains_preview():
    content = (ROOT / "ENTWICKLUNG.md").read_text(encoding="utf-8")
    assert "sc_digger/preview.py" in content
