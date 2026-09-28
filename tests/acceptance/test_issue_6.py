"""Akzeptanztests Issue #6: Lautheit als Tags, Audio bleibt unverändert."""
import subprocess
from pathlib import Path

import numpy as np
import pytest

from sc_digger.organize import loudness_tag_values, write_tags

# So liefert quality.check_file die Werte in t.quality_report
REPORT = {"integrated_lufs": -6.0, "true_peak_dbfs": 1.0, "loudness_range_lu": 5.3, "ok": True}
EXPECTED = {
    "REPLAYGAIN_TRACK_GAIN": "-12.00 dB",   # -18 LUFS Referenz - (-6.0 LUFS)
    "REPLAYGAIN_TRACK_PEAK": "1.122018",    # 10 ** (1.0 / 20), linear
    "SCDIGGER_LUFS": "-6.0",
    "SCDIGGER_LRA": "5.3",
}


def _audio(path: Path) -> Path:
    """Zwei Sekunden Rauschen im Zielformat (Endung bestimmt das Format)."""
    raw = path.with_suffix(".f32")
    raw.write_bytes((np.random.default_rng(6).standard_normal(44100 * 2) * 0.1).astype(np.float32).tobytes())
    extra = ["-b:a", "320k"] if path.suffix == ".mp3" else []
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", "44100", "-ac", "1",
                    "-i", str(raw), *extra, str(path)], check=True)
    return path


def _pcm(path: Path) -> bytes:
    """Dekodiertes Audio, um zu belegen, dass Tagging den Klang nicht verändert."""
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-"],
                          capture_output=True, check=True).stdout


def _read(path: Path) -> dict[str, str]:
    """Liest die Lautheits-Felder formatunabhängig zurück (Schlüssel in Großbuchstaben)."""
    ext = path.suffix.lower()
    out: dict[str, str] = {}
    if ext in (".mp3", ".wav", ".aiff"):
        import mutagen
        tags = mutagen.File(str(path)).tags
        for frame in tags.getall("TXXX"):
            out[frame.desc.upper()] = str(frame.text[0])
    elif ext == ".flac":
        from mutagen.flac import FLAC
        for k, v in FLAC(str(path)).tags:
            out[k.upper()] = v
    elif ext == ".m4a":
        from mutagen.mp4 import MP4
        for k, v in (MP4(str(path)).tags or {}).items():
            if k.startswith("----:com.apple.iTunes:"):
                out[k.split(":", 2)[2].upper()] = bytes(v[0]).decode("utf-8")
    return {k: v for k, v in out.items() if k in EXPECTED}


# ---------------- Werte berechnen ----------------
def test_values_replaygain2_and_own_fields():
    assert loudness_tag_values(REPORT) == EXPECTED


def test_values_rounding():
    v = loudness_tag_values({"integrated_lufs": -5.456, "true_peak_dbfs": -0.3, "loudness_range_lu": 3.46})
    assert v["REPLAYGAIN_TRACK_GAIN"] == "-12.54 dB"
    assert v["REPLAYGAIN_TRACK_PEAK"] == "0.966051"
    assert v["SCDIGGER_LUFS"] == "-5.5"
    assert v["SCDIGGER_LRA"] == "3.5"


def test_values_leave_out_what_was_not_measured():
    v = loudness_tag_values({"integrated_lufs": -6.0, "true_peak_dbfs": None, "loudness_range_lu": None})
    assert v == {"REPLAYGAIN_TRACK_GAIN": "-12.00 dB", "SCDIGGER_LUFS": "-6.0"}


@pytest.mark.parametrize("report", [None, {}, {"ok": False, "reason": "Fake"}])
def test_values_without_measurement_are_empty(report):
    assert loudness_tag_values(report) == {}


# ---------------- In Dateien schreiben ----------------
@pytest.mark.parametrize("ext", [".mp3", ".flac", ".aiff", ".m4a", ".wav"])
def test_roundtrip_all_formats(tmp_path, ext):
    p = _audio(tmp_path / f"t{ext}")
    assert write_tags(p, artist="Ueberrest", title="Surrender", bpm=155.0, loudness=REPORT) is True
    assert _read(p) == EXPECTED


@pytest.mark.parametrize("ext", [".mp3", ".flac", ".wav"])
def test_audio_is_not_changed(tmp_path, ext):
    """Leitprinzip: messen statt normalisieren."""
    p = _audio(tmp_path / f"t{ext}")
    before = _pcm(p)
    assert write_tags(p, artist="A", title="T", loudness=REPORT)
    assert _pcm(p) == before


def test_wav_gets_regular_tags_too(tmp_path):
    """WAV wurde bisher übersprungen; die meisten Originale kommen als WAV."""
    p = _audio(tmp_path / "t.wav")
    assert write_tags(p, artist="Ueberrest", title="Surrender", bpm=155.4, key_name="Cm") is True
    import mutagen
    tags = mutagen.File(str(p)).tags
    assert tags["TPE1"].text[0] == "Ueberrest"
    assert tags["TIT2"].text[0] == "Surrender"
    assert tags["TBPM"].text[0] == "155"
    assert tags["TKEY"].text[0] == "Cm"


def test_without_loudness_no_loudness_fields(tmp_path):
    p = _audio(tmp_path / "t.mp3")
    assert write_tags(p, artist="A", title="T") is True
    assert _read(p) == {}


def test_rewrite_replaces_old_values(tmp_path):
    p = _audio(tmp_path / "t.flac")
    write_tags(p, artist="A", title="T", loudness={"integrated_lufs": -9.0, "true_peak_dbfs": 0.0,
                                                   "loudness_range_lu": 7.0})
    write_tags(p, artist="A", title="T", loudness=REPORT)
    assert _read(p) == EXPECTED
