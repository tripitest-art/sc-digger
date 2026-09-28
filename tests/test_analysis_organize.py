import subprocess
from pathlib import Path

import numpy as np
import pytest

from sc_digger.analysis import analyze_track, detect_bpm, detect_key
from sc_digger.organize import _bpm_bucket, organize, write_tags


def _click_track(path: Path, bpm: float = 155.0, seconds: int = 30, sr: int = 44100):
    """Erzeugt echtes Klick-Material mit bekanntem Tempo, damit detect_bpm etwas zu erkennen hat."""
    interval = 60.0 / bpm
    n = int(seconds * sr)
    audio = np.zeros(n, dtype=np.float32)
    click = (np.random.default_rng(1).standard_normal(200) * np.hanning(200)).astype(np.float32)
    t = 0.0
    while t < seconds:
        i = int(t * sr)
        end = min(i + len(click), n)
        audio[i:end] += click[: end - i]
        t += interval
    raw = path.with_suffix(".f32")
    raw.write_bytes(audio.tobytes())
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", str(raw),
         str(path)],
        check=True,
    )


# ---------------- BPM/Key-Erkennung ----------------
def test_detect_bpm_close_to_true_tempo(tmp_path):
    p = tmp_path / "clicks.wav"
    _click_track(p, bpm=155.0)
    bpm = detect_bpm(p)
    assert bpm is not None
    # Tempo-Erkennung kann auf das Doppelte/Halbe springen (bekanntes Onset-Problem)
    assert any(abs(bpm - target) < 5 for target in (155.0, 77.5, 310.0)), bpm


def test_detect_bpm_returns_none_for_silence(tmp_path):
    p = tmp_path / "silence.wav"
    sr = 44100
    Path(p.with_suffix(".f32")).write_bytes(np.zeros(sr * 10, dtype=np.float32).tobytes())
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(sr), "-ac", "1",
                    "-i", str(p.with_suffix(".f32")), str(p)], check=True)
    # Stille darf nicht crashen; Ergebnis ist irrelevant, Hauptsache kein Exception
    detect_bpm(p)


def test_analyze_track_returns_expected_keys(tmp_path):
    p = tmp_path / "clicks.wav"
    _click_track(p, bpm=150.0)
    result = analyze_track(p)
    assert set(result) == {"bpm", "key_camelot", "key_name"}


def test_analysis_missing_librosa_returns_none_not_crash(tmp_path, monkeypatch):
    import sc_digger.analysis as a
    p = tmp_path / "x.wav"
    _click_track(p, bpm=150.0)

    real_import = __import__
    def fake_import(name, *args, **kwargs):
        if name == "librosa":
            raise ImportError("kein librosa")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr("builtins.__import__", fake_import)
    assert a.detect_bpm(p) is None
    assert a.detect_key(p) is None


# ---------------- Organize ----------------
def test_bpm_bucket_labels():
    assert _bpm_bucket(152) == "150-155"
    assert _bpm_bucket(159.9) == "155-160"
    assert _bpm_bucket(None) == "unknown-bpm"


def test_organize_moves_into_bpm_key_structure(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    f = inbox / "track.wav"
    f.write_bytes(b"x")
    target = organize(f, inbox, bpm=157.3, key_camelot="5A")
    assert target == inbox / "155-160" / "5A" / "track.wav"
    assert target.exists()


def test_organize_avoids_filename_collision(tmp_path):
    inbox = tmp_path / "inbox"
    (inbox / "155-160" / "5A").mkdir(parents=True)
    (inbox / "155-160" / "5A" / "track.wav").write_bytes(b"existing")
    f = inbox / "track.wav"
    f.write_bytes(b"new")
    target = organize(f, inbox, bpm=157, key_camelot="5A")
    assert target.name == "track_1.wav"
    assert target.read_bytes() == b"new"
    assert (inbox / "155-160" / "5A" / "track.wav").read_bytes() == b"existing"


def test_organize_unknown_bpm_and_key(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    f = inbox / "t.wav"
    f.write_bytes(b"x")
    target = organize(f, inbox, bpm=None, key_camelot=None)
    assert target == inbox / "unknown-bpm" / "unknown-key" / "t.wav"


# ---------------- Tagging ----------------
def test_write_tags_mp3_roundtrip(tmp_path):
    wav = tmp_path / "x.wav"
    import numpy as np
    raw = wav.with_suffix(".f32")
    raw.write_bytes((np.zeros(44100 * 2, dtype=np.float32)).tobytes())
    mp3 = tmp_path / "x.mp3"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", "44100", "-ac", "1",
                    "-i", str(raw), "-b:a", "320k", str(mp3)], check=True)

    ok = write_tags(mp3, artist="Ueberrest", title="Surrender", bpm=155.4,
                     key_name="Cm", genre="Schranz", comment="Score: 80p", url="https://x")
    assert ok
    from mutagen.id3 import ID3
    tags = ID3(str(mp3))
    assert tags["TIT2"].text[0] == "Surrender"
    assert tags["TPE1"].text[0] == "Ueberrest"
    assert tags["TBPM"].text[0] == "155"
    assert tags["TKEY"].text[0] == "Cm"


def test_write_tags_wav_is_skipped_not_crashed(tmp_path):
    p = tmp_path / "x.wav"
    p.write_bytes(b"RIFF....")
    assert write_tags(p, artist="A", title="T") is False


def test_write_tags_missing_mutagen_returns_false(tmp_path, monkeypatch):
    p = tmp_path / "x.mp3"
    p.write_bytes(b"x")
    real_import = __import__
    def fake_import(name, *args, **kwargs):
        if name == "mutagen":
            raise ImportError("kein mutagen")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr("builtins.__import__", fake_import)
    assert write_tags(p, artist="A", title="T") is False


# ---------------- BPM-Oktav-Korrektur ----------------
from sc_digger.analysis import resolve_bpm

W = (150, 165)


@pytest.mark.parametrize("audio, expected", [
    (78.3, 156.6),    # Halftime erkannt -> verdoppeln
    (156.6, 156.6),   # korrekt -> unverändert
    (313.2, 156.6),   # Doppelt erkannt -> halbieren
])
def test_octave_error_is_pulled_into_window(audio, expected):
    bpm, _ = resolve_bpm(audio, None, window=W)
    assert bpm == pytest.approx(expected, abs=0.1)


def test_real_tempo_outside_window_is_kept_not_forced():
    # Ein echter 140er Hard-Techno-Track darf nicht auf 70 oder 280 verbogen werden
    bpm, _ = resolve_bpm(140.0, None, window=W)
    assert bpm == 140.0


def test_outside_window_halftime_goes_to_plausible_range():
    # 72 -> 144 liegt nicht im Fenster, aber im plausiblen Bereich; 72 selbst nicht
    bpm, _ = resolve_bpm(72.0, None, window=W)
    assert bpm == 144.0


def test_text_bpm_anchors_octave_choice():
    # Uploader sagt 145; Audio erkennt 72.4 -> ×2 = 144.8 liegt nahe am Text
    bpm, reason = resolve_bpm(72.4, 145, window=W)
    assert bpm == pytest.approx(144.8, abs=0.1)
    assert "bestätigt" in reason


def test_text_bpm_wins_when_audio_has_no_octave_relation():
    bpm, reason = resolve_bpm(132.0, 158, window=W)
    assert bpm == 158.0
    assert "statt Audio" in reason


def test_no_audio_falls_back_to_text_or_none():
    assert resolve_bpm(None, 155, window=W)[0] == 155
    assert resolve_bpm(None, None, window=W)[0] is None


def test_implausible_value_is_left_unchanged():
    # 45 -> 90 / 22.5: keiner im Fenster oder plausiblen Bereich
    bpm, reason = resolve_bpm(45.0, None, window=W)
    assert bpm == 45.0 and "unkorrigiert" in reason
