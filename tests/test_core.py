import subprocess, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from sc_digger.collection import Collection
from sc_digger.models import Config, DownloadKind, Track
from sc_digger.pipeline import classify_download, estimate_bpm, filter_bpm, score_tracks
from sc_digger.quality import check_file

CFG = Config.load(Path(__file__).parent.parent / "config.yaml")


def mk(**kw) -> Track:
    base = dict(
        id=1, title="Test", url="https://soundcloud.com/a/test", artist="A",
        artist_url="", created_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        duration_ms=300000, genre="", tags=[], description="", bpm=None,
        plays=1000, likes=50, reposts=10, comments=2, downloadable=False,
        has_downloads_left=True, purchase_url=None, purchase_title=None,
    )
    base.update(kw)
    return Track(**base)


# ---------- Klassifizierung ----------
def test_native():
    assert classify_download(mk(downloadable=True)).download_kind == DownloadKind.NATIVE

def test_native_but_no_downloads_left_falls_through():
    t = mk(downloadable=True, has_downloads_left=False)
    assert classify_download(t).download_kind == DownloadKind.NONE

def test_hypeddit_via_purchase_url():
    t = classify_download(mk(purchase_url="https://hypeddit.com/abc"))
    assert t.download_kind == DownloadKind.HYPEDDIT

def test_droploud_in_description():
    t = classify_download(mk(description="FREE DL: https://droploud.com/x/y ok"))
    assert t.download_kind == DownloadKind.DROPLOUD

def test_store():
    t = classify_download(mk(purchase_url="https://artist.bandcamp.com/track/x"))
    assert t.download_kind == DownloadKind.STORE

def test_gate_beats_store():
    t = classify_download(mk(purchase_url="https://beatport.com/x",
                             description="gate https://hypeddit.com/z"))
    assert t.download_kind == DownloadKind.HYPEDDIT

def test_lookalike_host_not_matched():
    # notdroploud.com darf NICHT als droploud.com gelten
    t = classify_download(mk(purchase_url="https://notdroploud.com/x"))
    assert t.download_kind == DownloadKind.NONE

def test_none():
    assert classify_download(mk()).download_kind == DownloadKind.NONE


# ---------- BPM ----------
def test_bpm_from_title():
    assert estimate_bpm(mk(title="Track 155 BPM")) == 155.0

def test_bpm_filter_keeps_unknown_and_drops_out_of_range():
    ts = [mk(id=1, title="a 158 bpm"), mk(id=2, title="b 128 bpm"), mk(id=3, title="c")]
    kept = {t.id for t in filter_bpm(ts, CFG)}
    assert kept == {1, 3}


# ---------- Scoring ----------
def test_scoring_prefers_engagement_and_drops_spam():
    tracks = []
    for i in range(30):   # Durchschnittsmasse
        tracks.append(mk(id=100 + i, plays=2000, likes=30 + i, reposts=5))
    star = mk(id=1, plays=2000, likes=180, reposts=60)          # 9 % Likes
    spam = mk(id=2, plays=50000, likes=100, reposts=1)          # 0,2 %
    low = mk(id=3, plays=100, likes=50)                         # unter min_plays
    out = score_tracks(tracks + [star, spam, low], CFG)
    ids = [t.id for t in out]
    assert ids[0] == 1
    assert 2 not in ids and 3 not in ids


# ---------- Duplikat-Check ----------
def test_duplicate_and_remixer_distinction(tmp_path):
    (tmp_path / "Ueberrest - Surrender (DJØ Edit).wav").write_bytes(b"x")
    coll = Collection(tmp_path)
    same = mk(artist="Ueberrest", title="Surrender (DJØ Edit)")
    other = mk(artist="Ueberrest", title="Surrender (VRTGØ Edit)")
    unrelated = mk(artist="Foo", title="Completely Different Thing")
    assert coll.find_duplicate(same) is not None
    assert coll.find_duplicate(other) is None      # anderer Edit -> NICHT vorhanden
    assert coll.find_duplicate(unrelated) is None


# ---------- Qualitätsprüfung ----------
def _noise_wav(path: Path, lowpass_hz: int | None, seconds: int = 20):
    """Rauschen, optional per Lowpass hart bei lowpass_hz abgeschnitten."""
    rng = np.random.default_rng(0)
    n = 44100 * seconds
    raw = (rng.standard_normal(n).astype(np.float32) * 0.2)
    src = path.with_suffix(".f32")
    src.write_bytes(raw.tobytes())
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", "44100", "-ac", "1", "-i", str(src)]
    if lowpass_hz:
        cmd += ["-af", f"lowpass=f={lowpass_hz}:poles=2,lowpass=f={lowpass_hz}:poles=2,"
                       f"lowpass=f={lowpass_hz}:poles=2,lowpass=f={lowpass_hz}:poles=2"]
    subprocess.run(cmd + [str(path)], check=True)

def test_real_lossless_passes(tmp_path):
    p = tmp_path / "real.wav"
    _noise_wav(p, None)
    r = check_file(p, CFG)
    assert r["ok"], r

def test_fake_lossless_detected(tmp_path):
    p = tmp_path / "fake.wav"
    _noise_wav(p, 16000)          # typischer 128er-MP3-Cutoff
    r = check_file(p, CFG)
    assert not r["ok"] and "kHz" in r["reason"], r

def test_low_bitrate_mp3_rejected(tmp_path):
    wav = tmp_path / "x.wav"; _noise_wav(wav, None)
    mp3 = tmp_path / "x.mp3"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-b:a", "128k", str(mp3)], check=True)
    r = check_file(mp3, CFG)
    assert not r["ok"] and "128" in r["reason"] or "kbps" in r["reason"], r
