"""Tests für sc_digger/preview.py (Issue #118): lautestes Segment und Opus-Snippet.

Die Akzeptanztests decken den Normalfall ab; hier stehen die Randfälle: verschiedene
Eingangsformate, leere/kaputte Dateien, unbrauchbare Parameter, abbrechendes oder
fehlendes ffmpeg und die Aufräumarbeit bei halbfertigen Ausgabedateien.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sc_digger import preview
from sc_digger.preview import extract_preview, find_loudest_segment


def _run_ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def _make_burst(path: Path) -> Path:
    """20 s Audio: 5 s leises Rauschen, 10 s lauter Sinus, 5 s leises Rauschen."""
    filter_graph = (
        "anoisesrc=d=5:c=pink:r=22050:a=0.01[s1];"
        "sine=f=440:d=10:r=22050[s2];"
        "anoisesrc=d=5:c=pink:r=22050:a=0.01[s3];"
        "[s1][s2][s3]concat=n=3:v=0:a=1[out]"
    )
    _run_ffmpeg(["-filter_complex", filter_graph, "-map", "[out]", str(path)])
    return path


def _transcode(source: Path, target: Path, codec: str) -> Path:
    _run_ffmpeg(["-i", str(source), "-c:a", codec, str(target)])
    return target


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


@pytest.mark.parametrize(
    "name,codec",
    [("copy.wav", None), ("copy.flac", "flac"), ("copy.aiff", "pcm_s16be"), ("copy.mp3", "libmp3lame")],
)
def test_find_loudest_segment_works_for_all_input_formats(tmp_path, name, codec):
    wav = _make_burst(tmp_path / "burst.wav")
    source = wav if codec is None else _transcode(wav, tmp_path / name, codec)

    start = find_loudest_segment(source, segment_duration_s=5.0)

    assert 4.0 <= start <= 10.0


def test_find_loudest_segment_rejects_unusable_segment_durations(tmp_path):
    wav = _make_burst(tmp_path / "burst.wav")

    assert find_loudest_segment(wav, segment_duration_s=0.0) == 0.0
    assert find_loudest_segment(wav, segment_duration_s=-5.0) == 0.0


def test_find_loudest_segment_on_empty_and_corrupted_files(tmp_path):
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")

    corrupted = tmp_path / "corrupted.wav"
    corrupted.write_bytes(b"kein audio, nur text")

    assert find_loudest_segment(empty, 5.0) == 0.0
    assert find_loudest_segment(corrupted, 5.0) == 0.0
    assert find_loudest_segment(tmp_path, 5.0) == 0.0


def test_find_loudest_segment_survives_missing_ffmpeg(tmp_path, monkeypatch):
    wav = _make_burst(tmp_path / "burst.wav")

    def boom(*args, **kwargs):
        raise FileNotFoundError("ffmpeg fehlt")

    monkeypatch.setattr(preview.subprocess, "run", boom)
    assert find_loudest_segment(wav, 5.0) == 0.0


def test_find_loudest_segment_returns_zero_when_file_is_shorter_than_segment(tmp_path):
    """Kürzeres Audio als das Fenster -> 0.0, unabhängig vom Format."""
    short = tmp_path / "short.flac"
    _run_ffmpeg(["-f", "lavfi", "-i", "sine=f=440:d=8:r=22050", str(short)])

    assert find_loudest_segment(short, segment_duration_s=12.0) == 0.0


def test_find_loudest_segment_returns_valid_start_for_uniform_loudness(tmp_path):
    """Bei gleichmäßig lauter Datei ist jeder Start zulässig, aber nie außerhalb der Datei."""
    tone = tmp_path / "tone.flac"
    _run_ffmpeg(["-f", "lavfi", "-i", "sine=f=440:d=10:r=22050", str(tone)])

    start = find_loudest_segment(tone, segment_duration_s=4.0)

    assert 0.0 <= start <= 6.0


def test_extract_preview_creates_missing_directories(tmp_path):
    wav = _make_burst(tmp_path / "src.wav")
    out_ogg = tmp_path / "tief" / "verschachtelt" / "preview.ogg"

    assert extract_preview(wav, out_ogg, start_s=5.0, duration_s=3.0) is True
    assert out_ogg.is_file()


def test_extract_preview_truncates_to_requested_duration(tmp_path):
    wav = _make_burst(tmp_path / "src.wav")
    out_ogg = tmp_path / "short.ogg"

    assert extract_preview(wav, out_ogg, start_s=5.0, duration_s=4.0) is True
    assert 3.0 <= _probe_duration(out_ogg) <= 5.0


def test_extract_preview_rejects_corrupted_and_empty_input(tmp_path):
    corrupted = tmp_path / "kaputt.mp3"
    corrupted.write_bytes(b"nicht wirklich mp3")
    empty = tmp_path / "leer.wav"
    empty.write_bytes(b"")
    out_ogg = tmp_path / "out.ogg"

    assert extract_preview(corrupted, out_ogg) is False
    assert not out_ogg.exists()
    assert extract_preview(empty, out_ogg) is False
    assert not out_ogg.exists()
    assert extract_preview(tmp_path, out_ogg) is False


def test_extract_preview_returns_false_when_start_is_beyond_end(tmp_path):
    wav = _make_burst(tmp_path / "src.wav")
    out_ogg = tmp_path / "beyond.ogg"

    assert extract_preview(wav, out_ogg, start_s=120.0, duration_s=5.0) is False
    assert not out_ogg.exists()


def test_extract_preview_bitrate_shows_in_file_size(tmp_path):
    wav = _make_burst(tmp_path / "src.wav")
    small = tmp_path / "small.ogg"
    large = tmp_path / "large.ogg"

    assert extract_preview(wav, small, start_s=5.0, duration_s=10.0, bitrate_kbps=16) is True
    assert extract_preview(wav, large, start_s=5.0, duration_s=10.0, bitrate_kbps=96) is True

    assert 0 < small.stat().st_size < large.stat().st_size


def test_extract_preview_removes_partial_output_on_ffmpeg_error(tmp_path, monkeypatch):
    wav = _make_burst(tmp_path / "src.wav")
    out_ogg = tmp_path / "partial.ogg"

    def failing_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"halbfertig")
        return subprocess.CompletedProcess(cmd, 1, b"", b"Encoding failed")

    monkeypatch.setattr(preview.subprocess, "run", failing_run)

    assert extract_preview(wav, out_ogg, start_s=5.0, duration_s=3.0) is False
    assert not out_ogg.exists()


def test_extract_preview_survives_ffmpeg_timeout(tmp_path, monkeypatch):
    wav = _make_burst(tmp_path / "src.wav")
    out_ogg = tmp_path / "timeout.ogg"

    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1)

    monkeypatch.setattr(preview.subprocess, "run", hang)

    assert extract_preview(wav, out_ogg, start_s=5.0, duration_s=3.0) is False
    assert not out_ogg.exists()


def test_extract_preview_uses_loudest_segment_by_default(tmp_path):
    wav = _make_burst(tmp_path / "src.wav")
    out_ogg = tmp_path / "auto.ogg"

    assert extract_preview(wav, out_ogg, start_s=None, duration_s=5.0) is True
    assert out_ogg.stat().st_size > 500
