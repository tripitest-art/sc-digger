import subprocess
from pathlib import Path
import pytest
from sc_digger.models import Config
from sc_digger.quality import check_file, spectral_cutoff_hz

ROOT = Path(__file__).resolve().parents[2]
CFG = Config.load(ROOT / "config.yaml")


def _generate_wav(path: Path, lavfi: str, codec: str = "pcm_s16le", duration: int = 10, ar: int = 44100, extra: list[str] | None = None) -> Path:
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"{lavfi}:d={duration}",
        "-ac", "2", "-ar", str(ar), *(extra or ["-c:a", codec]), str(path),
    ]
    subprocess.run(cmd, check=True)
    return path


def test_silent_audio_cutoff_zero_is_rejected(tmp_path):
    silent_wav = _generate_wav(tmp_path / "silent.wav", "anullsrc=r=44100", duration=5)
    report = check_file(silent_wav, CFG)
    assert report["ok"] is False
    assert report["cutoff_hz"] == 0
    assert "nicht messbar" in report["reason"]


def test_lossy_codec_in_wav_container_is_rejected(tmp_path):
    fake_wav = _generate_wav(tmp_path / "fake_aac.wav", "anoisesrc=c=white:a=0.3", extra=["-c:a", "aac", "-b:a", "192k"], duration=5)
    report = check_file(fake_wav, CFG)
    assert report["ok"] is False
    assert "nicht akzeptiert" in report["reason"]


def test_float32_pcm_wav_is_recognized_as_lossless(tmp_path):
    f32_wav = _generate_wav(tmp_path / "float32.wav", "anoisesrc=c=white:a=0.3", codec="pcm_f32le", duration=5)
    report = check_file(f32_wav, CFG)
    assert report["codec"] == "pcm_f32le"
    assert report["ok"] is True
    assert report["reason"] == "echte Qualität"
    assert report["cutoff_hz"] is not None and report["cutoff_hz"] >= 19500


def test_multi_window_spectral_cutoff_picks_maximum(tmp_path):
    part1 = _generate_wav(tmp_path / "p1.wav", "anullsrc=r=44100", duration=4)
    part2 = _generate_wav(tmp_path / "p2.wav", "anoisesrc=c=white:a=0.3", duration=6)
    mixed = tmp_path / "intro_then_noise.wav"
    concat_txt = tmp_path / "concat.txt"
    concat_txt.write_text(f"file '{part1.resolve().as_posix()}'\nfile '{part2.resolve().as_posix()}'\n", encoding="utf-8")
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
        "-i", str(concat_txt), "-c", "copy", str(mixed)
    ], check=True)

    c_intro = spectral_cutoff_hz(mixed, seconds=2, offset=1)
    assert c_intro < 1000

    c_multi = spectral_cutoff_hz(mixed, seconds=2, offsets=(1, 6))
    assert c_multi >= 19500
