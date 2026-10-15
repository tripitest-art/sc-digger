"""Eigene Tests zu Issue #282: Stille/unmessbares Spektrum, Lossless nach Codec statt Endung,
Messung an mehreren Fenstern.

Alles synthetisch per ffmpeg, kein Netzwerk, Schreiben nur in tmp_path.
"""
import subprocess
from pathlib import Path

import pytest

from sc_digger.models import Config
from sc_digger.quality import (
    _MULTI_WINDOW_MIN_S,
    _cutoff_offsets,
    check_file,
    is_lossless_codec,
    spectral_cutoff_hz,
)

CFG = Config.load(Path(__file__).resolve().parent.parent / "config.yaml")


def _ffmpeg(path: Path, lavfi: str, *out_args: str, duration: float = 6, af: str | None = None) -> Path:
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"{lavfi}:d={duration}",
           "-ac", "2", "-ar", "44100"]
    if af:
        cmd += ["-af", af]
    subprocess.run(cmd + list(out_args) + [str(path)], check=True)
    return path


def _noise(path: Path, *out_args: str, duration: float = 6, af: str | None = None) -> Path:
    # Fester Seed (s=1): gleiches Rauschen bei jedem Lauf.
    return _ffmpeg(path, "anoisesrc=c=white:a=0.3:s=1", *out_args, duration=duration, af=af)


def _fake_from(src: Path, dst: Path, *out_args: str) -> Path:
    """Typischer Fake: 128-kbps-MP3 (harte Kante bei ~16 kHz), zurück in ein Lossless-Format dekodiert."""
    mp3 = dst.with_suffix(".128.mp3")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-c:a", "libmp3lame", "-b:a", "128k",
                    str(mp3)], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), *out_args, str(dst)], check=True)
    return dst


# ---------------------------------------------------------------- Codec-Erkennung
@pytest.mark.parametrize("codec", [
    "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_f64le", "pcm_s16be", "pcm_s24be",
    "flac", "alac", "wavpack", "ape", "tta",
])
def test_lossless_codecs_recognized(codec):
    assert is_lossless_codec(codec) is True


@pytest.mark.parametrize("codec", ["aac", "mp3", "opus", "vorbis", "ac3", "adpcm_ima_wav", "", None])
def test_lossy_or_unknown_codecs_not_lossless(codec):
    assert is_lossless_codec(codec) is False


# ---------------------------------------------------------------- 32-Bit-PCM und andere Formate
@pytest.mark.parametrize("codec", ["pcm_s32le", "pcm_s24le"])
def test_wide_pcm_wav_is_lossless_and_ok(tmp_path, codec):
    r = check_file(_noise(tmp_path / "wide.wav", "-c:a", codec), CFG)
    assert r["codec"] == codec
    assert r["ok"] is True and r["reason"] == "echte Qualität", r


def test_big_endian_pcm_aiff_is_ok(tmp_path):
    r = check_file(_noise(tmp_path / "x.aiff", "-c:a", "pcm_s16be"), CFG)
    assert r["codec"] == "pcm_s16be"
    assert r["ok"] is True, r


def test_flac_still_ok(tmp_path):
    r = check_file(_noise(tmp_path / "x.flac", "-c:a", "flac"), CFG)
    assert r["ok"] is True and r["cutoff_hz"] >= 19500, r


def test_fake_float32_wav_still_rejected_by_spectrum(tmp_path):
    # Mehr Bits machen aus einer hochkonvertierten Quelle keinen Lossless-Track.
    src = _noise(tmp_path / "src.wav", "-c:a", "pcm_s16le")
    r = check_file(_fake_from(src, tmp_path / "fake32.wav", "-c:a", "pcm_f32le"), CFG)
    assert r["ok"] is False
    assert r["reason"].startswith("Lossless-Container, aber Spektrum endet bei"), r


# ---------------------------------------------------------------- Lossy-Stream hinter Lossless-Endung
@pytest.mark.parametrize("name,args", [
    ("aac.wav", ("-c:a", "aac", "-b:a", "192k")),
    ("mp3.wav", ("-c:a", "libmp3lame", "-b:a", "320k")),
])
def test_lossy_stream_in_lossless_container_rejected(tmp_path, name, args):
    r = check_file(_noise(tmp_path / name, *args), CFG)
    assert r["ok"] is False
    assert "nicht akzeptiert" in r["reason"] and ".wav" in r["reason"], r
    assert r["cutoff_hz"] is None   # kein Spektrum nötig, schon der Codec reicht


def test_lossless_codec_behind_unlisted_extension_rejected_with_clear_reason(tmp_path):
    # FLAC in einer .mka-Datei: Codec ist verlustfrei, die Endung steht aber nicht in accepted_lossless.
    r = check_file(_noise(tmp_path / "x.mka", "-c:a", "flac"), CFG)
    assert r["ok"] is False
    assert "accepted_lossless" in r["reason"], r


def test_real_mp3_unchanged(tmp_path):
    r = check_file(_noise(tmp_path / "x.mp3", "-c:a", "libmp3lame", "-b:a", "320k"), CFG)
    assert r["ok"] is True and r["reason"] == "echte Qualität", r


# ---------------------------------------------------------------- Stille und unmessbares Material
def test_silent_mp3_rejected_as_not_measurable(tmp_path):
    r = check_file(_ffmpeg(tmp_path / "silent.mp3", "anullsrc=r=44100", "-c:a", "libmp3lame",
                           "-b:a", "320k", duration=5), CFG)
    assert r["ok"] is False and r["cutoff_hz"] == 0
    assert r["reason"] == "Spektrum nicht messbar (Stille oder kein Signal)", r


def test_signal_below_noise_floor_counts_as_silence(tmp_path):
    # Rauschen mit Amplitude 1e-5 (≈ -100 dBFS) liegt unter der Messschwelle.
    quiet = _ffmpeg(tmp_path / "quiet.wav", "anoisesrc=c=white:a=0.00001", "-c:a", "pcm_f32le", duration=5)
    assert spectral_cutoff_hz(quiet) == 0.0
    assert check_file(quiet, CFG)["ok"] is False


def test_too_short_file_is_not_measurable(tmp_path):
    # Unter 3 s ist die Messung nicht belastbar; bisher wurde so eine Datei still freigegeben.
    r = check_file(_noise(tmp_path / "short.wav", "-c:a", "pcm_s16le", duration=1), CFG)
    assert r["ok"] is False and "nicht messbar" in r["reason"], r


def test_silence_returns_zero(tmp_path):
    silent = _ffmpeg(tmp_path / "s.wav", "anullsrc=r=44100", "-c:a", "pcm_s16le", duration=5)
    assert spectral_cutoff_hz(silent) == 0.0
    assert spectral_cutoff_hz(silent, offsets=(0, 1)) == 0.0


# ---------------------------------------------------------------- Mehrere Messfenster
def test_offsets_behind_file_end_fall_back_to_start(tmp_path):
    noise = _noise(tmp_path / "n.wav", "-c:a", "pcm_s16le", duration=6)
    assert spectral_cutoff_hz(noise, offsets=(100, 200)) >= 19500


def test_offsets_ignore_windows_behind_end_but_keep_valid_ones(tmp_path):
    noise = _noise(tmp_path / "n.wav", "-c:a", "pcm_s16le", duration=8)
    assert spectral_cutoff_hz(noise, seconds=2, offsets=(1, 500)) >= 19500


def test_empty_offsets_behave_like_default(tmp_path):
    noise = _noise(tmp_path / "n.wav", "-c:a", "pcm_s16le", duration=6)
    assert spectral_cutoff_hz(noise, offsets=[]) == spectral_cutoff_hz(noise)


def test_max_of_windows_does_not_hide_a_fake(tmp_path):
    # Überall bei 16 kHz abgeschnitten: auch das Maximum bleibt darunter -> Fake bleibt Fake.
    src = _noise(tmp_path / "src.wav", "-c:a", "pcm_s16le", duration=10)
    fake = _fake_from(src, tmp_path / "fake.wav", "-c:a", "pcm_s16le")
    assert spectral_cutoff_hz(fake, seconds=3, offsets=(1, 6)) < 19000


@pytest.mark.parametrize("duration", [0, 60, 149.9])
def test_cutoff_offsets_single_window_for_short_tracks(duration):
    assert _cutoff_offsets(duration) is None


@pytest.mark.parametrize("duration,expected", [(150, (60, 90)), (300, (60, 180)), (3600, (60, 2160))])
def test_cutoff_offsets_two_windows_for_long_tracks(duration, expected):
    assert _cutoff_offsets(duration) == expected


@pytest.mark.parametrize("duration", [150, 151.5, 200, 420, 900])
def test_cutoff_offsets_windows_fit_and_do_not_overlap(duration):
    first, second = _cutoff_offsets(duration)
    assert second >= first + 30           # kein Überlapp bei 30-s-Fenstern
    assert second + 30 <= duration        # zweites Fenster liegt komplett im Track


def test_threshold_matches_documented_value():
    assert _MULTI_WINDOW_MIN_S == 150


def _gated_noise(path: Path, quiet_until_s: int, duration: int) -> Path:
    """Stille bis quiet_until_s, danach Rauschen."""
    gate = f"volume=enable='lt(t,{quiet_until_s})':volume=0"
    return _noise(path, "-c:a", "pcm_s16le", duration=duration, af=gate)


def test_check_file_survives_long_quiet_intro(tmp_path):
    # 160 s, die ersten 95 s still: das alte starre Fenster bei 60 s fiele in die Stille und
    # meldete "nicht messbar". Mit dem zweiten Fenster (96 s) wird der laute Teil gemessen.
    p = _gated_noise(tmp_path / "ambient_intro.wav", quiet_until_s=95, duration=160)
    assert spectral_cutoff_hz(p) == 0.0                       # Standardfenster allein täuscht
    r = check_file(p, CFG)
    assert r["ok"] is True and r["cutoff_hz"] >= 19500, r


def test_check_file_long_fake_with_quiet_intro_still_rejected(tmp_path):
    src = _gated_noise(tmp_path / "src.wav", quiet_until_s=95, duration=160)
    r = check_file(_fake_from(src, tmp_path / "fake_long.wav", "-c:a", "pcm_s16le"), CFG)
    assert r["ok"] is False
    assert r["reason"].startswith("Lossless-Container, aber Spektrum endet bei"), r
