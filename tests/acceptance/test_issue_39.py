"""Akzeptanztests: VBR-MP3 (z. B. LAME V0, im Schnitt 240–260 kbps) nach Spektrum statt nach Bitrate bewerten."""
import subprocess
from pathlib import Path

import pytest
from mutagen.id3 import APIC, ID3, TIT2

from sc_digger.models import Config
from sc_digger.quality import check_file, is_vbr_mp3

CFG = Config.load(Path(__file__).resolve().parents[2] / "config.yaml")


def _mp3(path: Path, *args: str) -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=8:c=pink:r=44100:a=0.3",
                    "-ac", "2", "-c:a", "libmp3lame", *args, str(path)], check=True)
    return path


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("vbr")
    return {
        "v0": _mp3(d / "v0.mp3", "-q:a", "0"),
        "v7": _mp3(d / "v7.mp3", "-q:a", "7"),
        "cbr320": _mp3(d / "cbr320.mp3", "-b:a", "320k"),
        "cbr256": _mp3(d / "cbr256.mp3", "-b:a", "256k"),
    }


def test_is_vbr_mp3(files):
    assert is_vbr_mp3(files["v0"]) is True
    assert is_vbr_mp3(files["v7"]) is True
    assert is_vbr_mp3(files["cbr320"]) is False
    assert is_vbr_mp3(files["cbr256"]) is False


def test_is_vbr_mp3_skips_id3_tag(files, tmp_path):
    big = tmp_path / "v0_cover.mp3"
    big.write_bytes(files["v0"].read_bytes())
    tags = ID3()
    tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=b"\x00" * 200_000))
    tags.save(big)
    assert is_vbr_mp3(big) is True                    # Xing-Kopf liegt hinter 200 kB Cover

    cbr = tmp_path / "cbr_xing_title.mp3"
    cbr.write_bytes(files["cbr256"].read_bytes())
    tags = ID3()
    tags.add(TIT2(encoding=3, text="Xing VBRI"))
    tags.save(cbr)
    assert is_vbr_mp3(cbr) is False                   # Text im Tag zählt nicht


def test_is_vbr_mp3_never_raises(tmp_path):
    assert is_vbr_mp3(tmp_path / "fehlt.mp3") is False
    short = tmp_path / "kurz.mp3"
    short.write_bytes(b"ID3\x04\x00\x00\x7f\x7f\x7f\x7f")   # Tag-Größe zeigt über das Dateiende
    assert is_vbr_mp3(short) is False


def test_vbr_with_full_spectrum_is_ok(files):
    r = check_file(files["v0"], CFG)
    assert r["bitrate_kbps"] < 320                    # Bitrate allein hätte abgelehnt
    assert r["ok"] is True and r["cutoff_hz"] >= 19000


def test_vbr_with_low_cutoff_is_rejected_by_spectrum(files):
    r = check_file(files["v7"], CFG)
    assert r["ok"] is False
    assert r["cutoff_hz"] is not None and r["cutoff_hz"] < 19000
    assert r["reason"].startswith("VBR-MP3, aber Spektrum endet bei")


def test_cbr_below_320_still_rejected_by_bitrate(files):
    r = check_file(files["cbr256"], CFG)
    assert r["ok"] is False and r["reason"] == "MP3 mit nur 256 kbps"
    assert r["cutoff_hz"] is None                     # kein Spektrum nötig


def test_cbr_320_unchanged(files):
    r = check_file(files["cbr320"], CFG)
    assert r["ok"] is True and r["reason"] == "echte Qualität"
