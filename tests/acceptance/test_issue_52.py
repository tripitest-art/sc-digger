"""Akzeptanztests: Energie-Klassifizierung (1-5 Stufen), Teil 1 (Issue #52)."""
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from mutagen.flac import FLAC
from mutagen.id3 import ID3, TXXX

from sc_digger.analysis import compute_energy_features
from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Track
from sc_digger.organize import read_energy_tag, write_tags
from sc_digger.style import Subgenre, calculate_energy, classify_track


def _make_synth_wav(path: Path, freq: float = 1000.0, duration: float = 6.0, sr: int = 22050) -> Path:
    """Erzeugt eine synthetische WAV-Datei mit Sinuston bestimmter Frequenz."""
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    sig = (0.5 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(sig.tobytes())
    return path


def _make_audio_file(path: Path) -> Path:
    """Erzeugt eine kurze Testdatei (MP3 oder FLAC) via ffmpeg."""
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ["-b:a", "320k"] if path.suffix == ".mp3" else []
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "anoisesrc=d=1:c=pink:r=44100:a=0.2", "-ac", "2", *args, str(path)],
        check=True,
    )
    return path


def test_compute_energy_features_synthetisch(tmp_path):
    p_low = _make_synth_wav(tmp_path / "low.wav", freq=1000.0)
    res_low = compute_energy_features(p_low)
    assert isinstance(res_low, dict)
    assert set(res_low.keys()) == {"spectral_centroid_hz", "rms"}
    assert res_low["spectral_centroid_hz"] is not None
    assert 500.0 < res_low["spectral_centroid_hz"] < 2000.0
    assert res_low["rms"] is not None and res_low["rms"] > 0.0

    p_high = _make_synth_wav(tmp_path / "high.wav", freq=5000.0)
    res_high = compute_energy_features(p_high)
    assert res_high["spectral_centroid_hz"] is not None
    assert res_high["spectral_centroid_hz"] > 3500.0
    assert res_high["rms"] is not None and res_high["rms"] > 0.0


def test_compute_energy_features_fehlerfaelle(tmp_path):
    res_missing = compute_energy_features(tmp_path / "nicht_vorhanden.wav")
    assert res_missing == {"spectral_centroid_hz": None, "rms": None}

    corrupt = tmp_path / "corrupt.wav"
    corrupt.write_bytes(b"ungueltiges audiomaterial")
    res_corrupt = compute_energy_features(corrupt)
    assert res_corrupt == {"spectral_centroid_hz": None, "rms": None}


def test_calculate_energy_bpm_basis_stufen():
    assert calculate_energy(130.0) == 1
    assert calculate_energy(137.9) == 1
    assert calculate_energy(138.0) == 2
    assert calculate_energy(144.9) == 2
    assert calculate_energy(145.0) == 3
    assert calculate_energy(151.9) == 3
    assert calculate_energy(152.0) == 4
    assert calculate_energy(159.9) == 4
    assert calculate_energy(160.0) == 5
    assert calculate_energy(175.0) == 5
    assert calculate_energy(None) == 3


def test_calculate_energy_modifikatoren_und_klammerung():
    # LUFS > -6.0 -> +1
    assert calculate_energy(148.0, lufs=-5.5) == 4
    assert calculate_energy(148.0, lufs=-6.0) == 3

    # LRA < 3.0 -> +1, LRA > 6.5 -> -1
    assert calculate_energy(148.0, lra=2.5) == 4
    assert calculate_energy(148.0, lra=7.0) == 2
    assert calculate_energy(148.0, lra=5.0) == 3

    # Spectral Centroid: > 3500.0 -> +1, < 1800.0 -> -1
    assert calculate_energy(148.0, spectral_centroid_hz=4000.0) == 4
    assert calculate_energy(148.0, spectral_centroid_hz=1500.0) == 2
    assert calculate_energy(148.0, spectral_centroid_hz=2500.0) == 3

    # Subgenre: Terror/Crossbreed -> +1, Melodic/EBM -> -1
    assert calculate_energy(148.0, subgenre=Subgenre.TERROR) == 4
    assert calculate_energy(148.0, subgenre=Subgenre.CROSSBREED) == 4
    assert calculate_energy(148.0, subgenre=Subgenre.MELODIC) == 2
    assert calculate_energy(148.0, subgenre=Subgenre.EBM) == 2

    # Klammerung auf 1..5
    assert calculate_energy(130.0, lra=8.0, spectral_centroid_hz=1000.0, subgenre=Subgenre.MELODIC) == 1
    assert calculate_energy(165.0, lufs=-4.0, lra=2.0, spectral_centroid_hz=4000.0, subgenre=Subgenre.TERROR) == 5


def test_classify_track_weitergabe_energy_features():
    res_bright = classify_track(148.0, spectral_centroid_hz=4000.0, rms=0.25)
    assert res_bright.energy == 4

    res_dark = classify_track(148.0, spectral_centroid_hz=1500.0, rms=0.1)
    assert res_dark.energy == 2


def test_write_tags_und_read_energy_tag_mp3(tmp_path):
    p = _make_audio_file(tmp_path / "track.mp3")
    assert read_energy_tag(p) is None

    assert write_tags(p, artist="Artist", title="Track", bpm=150.0, energy=4) is True
    assert read_energy_tag(p) == 4

    tags = ID3(str(p))
    frame = tags.get("TXXX:SCDIGGER_ENERGY")
    assert frame is not None and frame.text == ["4"]


def test_write_tags_und_read_energy_tag_flac(tmp_path):
    p = _make_audio_file(tmp_path / "track.flac")
    assert read_energy_tag(p) is None

    assert write_tags(p, artist="Artist", title="Track", bpm=150.0, energy=2) is True
    assert read_energy_tag(p) == 2

    audio = FLAC(str(p))
    vals = audio.get("scdigger_energy") or audio.get("SCDIGGER_ENERGY")
    assert vals == ["2"]


def test_read_energy_tag_ungueltige_werte(tmp_path):
    assert read_energy_tag(tmp_path / "nicht_da.mp3") is None

    p = _make_audio_file(tmp_path / "invalid.mp3")
    tags = ID3(str(p))

    tags.add(TXXX(encoding=3, desc="SCDIGGER_ENERGY", text=["6"]))
    tags.save(str(p))
    assert read_energy_tag(p) is None

    tags.add(TXXX(encoding=3, desc="SCDIGGER_ENERGY", text=["0"]))
    tags.save(str(p))
    assert read_energy_tag(p) is None

    tags.add(TXXX(encoding=3, desc="SCDIGGER_ENERGY", text=["hoch"]))
    tags.save(str(p))
    assert read_energy_tag(p) is None


def test_trackdb_migration_10_und_set_energy(tmp_path):
    db_file = tmp_path / "tracks.sqlite"
    with TrackDB(db_file) as db:
        migrations = [row[0] for row in db.db.execute("SELECT version FROM schema_migrations").fetchall()]
        assert 10 in migrations

        rec = db.upsert_track(TrackRecord(
            path="/music/inbox/150/8A/Track 1.wav",
            mtime=1700000000.0,
            size=1000,
            artist="Artist A",
            title="Track 1",
            status=TrackStatus.INBOX,
            energy=3,
        ))
        assert rec.energy == 3

        fetched = db.get_track_by_id(rec.id)
        assert fetched is not None
        assert fetched.energy == 3

        ok = db.set_energy(rec.id, 5)
        assert ok is True

        updated = db.get_track_by_id(rec.id)
        assert updated is not None
        assert updated.energy == 5

        assert db.set_energy(999999, 4) is False


def test_track_model_energy_feld():
    t = Track(
        id=1,
        title="Track",
        url="https://soundcloud.com/artist/track",
        artist="Artist",
        artist_url="https://soundcloud.com/artist",
        created_at="2026-01-01T00:00:00Z",
        duration_ms=180000,
        genre="Schranz",
        tags=["schranz"],
        description="",
        bpm=150.0,
        plays=100,
        likes=10,
        reposts=2,
        comments=1,
        downloadable=True,
        has_downloads_left=True,
        purchase_url=None,
        purchase_title=None,
    )
    assert t.energy is None
    t.energy = 4
    assert t.energy == 4
