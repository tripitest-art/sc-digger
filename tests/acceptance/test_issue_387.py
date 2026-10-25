import subprocess
from pathlib import Path

from sc_digger.loudness import apply_gain
from sc_digger.models import Config
from sc_digger.quality import check_file
from sc_digger.schema import get_field

ROOT = Path(__file__).resolve().parents[2]
CFG = Config.load(ROOT / "config.yaml")


def _noise(path: Path, *codec_args: str) -> Path:
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=c=white:a=0.3:s=1:d=6",
           "-ac", "2", "-ar", "44100", *codec_args, str(path)]
    subprocess.run(cmd, check=True)
    return path


def _recode(src: Path, dst: Path, *codec_args: str) -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), *codec_args, str(dst)], check=True)
    return dst


def test_default_config_lists_m4a_and_keeps_existing_formats():
    accepted = CFG["quality"]["accepted_lossless"]
    assert {"wav", "aiff", "aif", "flac", "m4a"} <= set(accepted)
    assert "mp3" not in accepted and "aac" not in accepted


def test_real_alac_in_m4a_is_accepted(tmp_path):
    report = check_file(_noise(tmp_path / "x.m4a", "-c:a", "alac"), CFG)
    assert report["codec"] == "alac"
    assert report["ext"] == "m4a"
    assert report["ok"] is True
    assert report["reason"] == "echte Qualität"


def test_aac_in_m4a_is_still_rejected(tmp_path):
    report = check_file(_noise(tmp_path / "aac.m4a", "-c:a", "aac", "-b:a", "256k"), CFG)
    assert report["ok"] is False
    assert "nicht akzeptiert" in report["reason"]


def test_alac_made_from_a_128k_mp3_is_rejected_by_spectrum(tmp_path):
    mp3 = _noise(tmp_path / "src.mp3", "-c:a", "libmp3lame", "-b:a", "128k")
    report = check_file(_recode(mp3, tmp_path / "fake.m4a", "-c:a", "alac"), CFG)
    assert report["codec"] == "alac"
    assert report["ok"] is False
    assert "Spektrum endet" in report["reason"]


def test_help_text_explains_the_m4a_default():
    field = get_field("quality.accepted_lossless")
    assert field is not None and field.kind == "list"
    for part in ("Standard: wav, aiff, aif, flac, m4a", "ALAC", "AAC"):
        assert part in field.help, part
    assert "kann m4a ergänzt werden" not in field.help


def test_loudness_gain_still_leaves_m4a_untouched(tmp_path):
    path = _noise(tmp_path / "x.m4a", "-c:a", "alac")
    before = path.read_bytes()
    assert apply_gain(path, 3.0) is False
    assert path.read_bytes() == before
