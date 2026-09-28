"""Tests für den Library-Audit Modus (Phase 2.2)."""
import hashlib
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sc_digger.audit import (
    AuditSummary,
    audit_collection,
    audit_file,
    generate_html_report,
    read_existing_tags,
    run_audit,
)
from sc_digger.db import QualityStatus, TrackDB
from sc_digger.models import Config


@pytest.fixture
def test_cfg(tmp_path: Path) -> Config:
    cfg_data = {
        "quality": {
            "accepted_lossless": ["wav", "aiff", "aif", "flac"],
            "min_mp3_bitrate_kbps": 320,
            "min_cutoff_hz_for_320": 19000,
            "min_cutoff_hz_for_lossless": 19500,
            "min_loudness_range_lu": 2.5,
            "max_true_peak_dbfs": 3.0,
        },
        "download": {
            "collection_dir": str(tmp_path / "collection"),
            "inbox_dir": str(tmp_path / "inbox"),
        },
        "state": {
            "db_path": str(tmp_path / "test_state.sqlite"),
            "track_db_path": str(tmp_path / "test_tracks.sqlite"),
        },
    }
    return Config(cfg_data)


def test_audit_file_detection_and_db_storage(tmp_path: Path, test_cfg: Config):
    """Testet die Prüfung einer Datei inkl. Speicherung in der TrackDB."""
    db_file = tmp_path / "tracks.sqlite"
    fake_file = tmp_path / "fake_transcode.mp3"
    fake_file.write_bytes(b"dummy audio")

    fake_report = {
        "file": fake_file.name,
        "codec": "mp3",
        "ext": "mp3",
        "bitrate_kbps": 320,
        "sample_rate": 44100,
        "cutoff_hz": 15200,  # 15.2 kHz = klares Indiz für Fake 320k
        "ok": False,
        "reason": "320er MP3, aber Spektrum endet bei 15.2 kHz",
        "integrated_lufs": -7.1,
        "true_peak_dbfs": 0.5,
        "loudness_range_lu": 3.8,
        "clipped": False,
    }

    with TrackDB(db_file) as db:
        with patch("sc_digger.audit.check_file", return_value=fake_report), \
             patch("sc_digger.audit.read_existing_tags", return_value={"bpm": 150.0, "key": "8A"}):
            res = audit_file(fake_file, test_cfg, db)

        assert res.status == QualityStatus.FAKE_TRANSCODE
        assert "15.2 kHz" in res.reason
        assert res.cached is False

        # In DB prüfen
        record = db.get_track_by_path(fake_file)
        assert record is not None
        assert record.quality_status == QualityStatus.FAKE_TRANSCODE.value
        assert record.cutoff_hz == 15200
        assert record.bpm == 150.0


def test_incremental_collection_audit(tmp_path: Path, test_cfg: Config):
    """Testet, dass unveränderte Dateien beim zweiten Lauf aus dem Cache genommen werden."""
    coll_dir = tmp_path / "collection"
    coll_dir.mkdir(parents=True)
    db_file = tmp_path / "audit.sqlite"

    f1 = coll_dir / "clean_track.flac"
    f2 = coll_dir / "transcode.mp3"
    f1.write_bytes(b"audio flac")
    f2.write_bytes(b"audio mp3")

    report_clean = {
        "file": f1.name, "codec": "flac", "ext": "flac", "bitrate_kbps": 1000,
        "cutoff_hz": 21000, "ok": True, "reason": "echte Qualität",
        "integrated_lufs": -6.0, "true_peak_dbfs": 0.1, "loudness_range_lu": 4.0, "clipped": False,
    }
    report_fake = {
        "file": f2.name, "codec": "mp3", "ext": "mp3", "bitrate_kbps": 320,
        "cutoff_hz": 14000, "ok": False, "reason": "Cutoff 14 kHz",
        "integrated_lufs": -8.0, "true_peak_dbfs": -0.5, "loudness_range_lu": 5.0, "clipped": False,
    }

    def mock_check(path, cfg):
        return report_clean if path.name == f1.name else report_fake

    with TrackDB(db_file) as db:
        with patch("sc_digger.audit.check_file", side_effect=mock_check), \
             patch("sc_digger.audit.read_existing_tags", return_value={"bpm": 155.0, "key": "9B"}):

            # Erster Lauf: 2 gescannt, 0 aus Cache
            s1 = audit_collection(coll_dir, test_cfg, db)
            assert s1.total_files == 2
            assert s1.scanned == 2
            assert s1.cached == 0
            assert s1.ok == 1
            assert s1.fakes == 1

            # Zweiter Lauf ohne Änderungen: 0 gescannt, 2 aus Cache
            s2 = audit_collection(coll_dir, test_cfg, db)
            assert s2.total_files == 2
            assert s2.scanned == 0
            assert s2.cached == 2
            assert s2.ok == 1
            assert s2.fakes == 1

            # Dritter Lauf mit force=True: 2 neu gescannt
            s3 = audit_collection(coll_dir, test_cfg, db, force=True)
            assert s3.scanned == 2
            assert s3.cached == 0


def test_generate_html_report(tmp_path: Path):
    """Prüft Erstellung und Inhalt des HTML-Audit-Reports."""
    summary = AuditSummary(
        total_files=2,
        scanned=2,
        cached=0,
        ok=1,
        fakes=1,
        duration_sec=1.5,
    )
    r1 = MagicMock(
        path=Path("/music/Schranz/good.flac"),
        status=QualityStatus.OK,
        format="flac",
        bitrate_kbps=1411,
        cutoff_hz=21500,
        bpm=155.0,
        key_camelot="8A",
        key_name="Am",
        lufs=-6.2,
        true_peak_dbfs=0.2,
        reason="echte Qualität",
        cached=False,
    )
    r2 = MagicMock(
        path=Path("/music/Schranz/fake.mp3"),
        status=QualityStatus.FAKE_TRANSCODE,
        format="mp3",
        bitrate_kbps=320,
        cutoff_hz=15000,
        bpm=150.0,
        key_camelot="9B",
        key_name="Gm",
        lufs=-8.0,
        true_peak_dbfs=-0.1,
        reason="Cutoff bei 15.0 kHz",
        cached=False,
    )
    summary.results = [r1, r2]

    html_text = generate_html_report(summary, "/music/Schranz")
    assert "<!DOCTYPE html>" in html_text
    assert "sc-digger Library Audit Report" in html_text
    assert "good.flac" in html_text
    assert "fake.mp3" in html_text
    assert "FAKE TRANSCODE" in html_text
    assert "21.5 kHz" in html_text
    assert "15.0 kHz" in html_text


def test_run_audit_command(tmp_path: Path, test_cfg: Config, capsys):
    """Testet den CLI-Einstiegspunkt run_audit mit Report-Generierung."""
    coll_dir = tmp_path / "collection"
    coll_dir.mkdir(parents=True)
    report_file = tmp_path / "report.html"

    test_file = coll_dir / "track.wav"
    test_file.write_bytes(b"wav")

    report_mock = {
        "file": test_file.name, "codec": "pcm_s16le", "ext": "wav",
        "bitrate_kbps": 1411, "cutoff_hz": 21000, "ok": True,
        "reason": "echte Qualität", "integrated_lufs": -6.0,
        "true_peak_dbfs": 0.0, "loudness_range_lu": 4.0, "clipped": False,
    }

    with patch("sc_digger.audit.check_file", return_value=report_mock), \
         patch("sc_digger.audit.read_existing_tags", return_value={"bpm": 155.0, "key": "8A"}):
        summary = run_audit(test_cfg, path=coll_dir, report_path=report_file)

    assert summary.total_files == 1
    assert summary.ok == 1
    assert report_file.exists()
    out = capsys.readouterr().out
    assert "sc-digger Library Audit Report" in out
    assert "Tracks Gesamt:     1" in out


def test_audit_leaves_collection_completely_untouched(tmp_path: Path, test_cfg: Config):
    """Prüft strikte Read-Only-Integrität: mtime, Dateigröße und SHA256-Hash müssen unverändert sein."""
    coll_dir = tmp_path / "collection"
    coll_dir.mkdir(parents=True)
    sub_dir = coll_dir / "Deep" / "155"
    sub_dir.mkdir(parents=True)

    f1 = coll_dir / "track1.mp3"
    f2 = sub_dir / "track2.flac"
    f3 = sub_dir / "track3.wav"

    f1.write_bytes(b"dummy mp3 data \x00\x01\x02\xff")
    f2.write_bytes(b"dummy flac data \x10\x20\x30\x40")
    f3.write_bytes(b"dummy wav data \xaa\xbb\xcc\xdd")

    files = [f1, f2, f3]

    # Zustand vor dem Audit erfassen
    before = {}
    for f in files:
        stat = os.stat(f)
        before[f] = {
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
        }

    mock_report = {
        "codec": "mp3", "ext": "mp3", "bitrate_kbps": 320, "cutoff_hz": 20000,
        "ok": True, "reason": "ok", "integrated_lufs": -6.0, "true_peak_dbfs": 0.0,
        "loudness_range_lu": 4.0, "clipped": False,
    }

    with patch("sc_digger.audit.check_file", return_value=mock_report), \
         patch("sc_digger.audit.read_existing_tags", return_value={"bpm": 155.0, "key": "8A"}):
        summary = run_audit(test_cfg, path=coll_dir, report_path=tmp_path / "report.html")

    assert summary.total_files == 3
    assert summary.ok == 3

    # Prüfen, dass sich keine Datei geändert hat und keine neuen Dateien angelegt wurden
    after_files = sorted(coll_dir.rglob("*"))
    assert set(f for f in after_files if f.is_file()) == set(files)

    for f in files:
        stat = os.stat(f)
        assert stat.st_mtime_ns == before[f]["mtime_ns"], f"mtime von {f.name} verändert!"
        assert stat.st_size == before[f]["size"], f"Größe von {f.name} verändert!"
        assert hashlib.sha256(f.read_bytes()).hexdigest() == before[f]["sha256"], f"Inhalt von {f.name} verändert!"


def test_run_audit_rejects_report_inside_collection(tmp_path: Path, test_cfg: Config):
    """Stellt sicher, dass --report niemals in /music/Schranz oder dem Zielordner landet."""
    coll_dir = tmp_path / "collection"
    coll_dir.mkdir(parents=True)
    (coll_dir / "t.mp3").write_bytes(b"data")

    # 1. Direkt im Zielordner
    bad_report = coll_dir / "report.html"
    with pytest.raises(ValueError, match="Report-Pfad darf nicht im Sammlungsordner liegen"):
        run_audit(test_cfg, path=coll_dir, report_path=bad_report)
    assert not bad_report.exists()

    # 2. In einem Unterordner des Sammlungsordners
    bad_nested_report = coll_dir / "sub" / "report.html"
    with pytest.raises(ValueError, match="Report-Pfad darf nicht im Sammlungsordner liegen"):
        run_audit(test_cfg, path=coll_dir, report_path=bad_nested_report)
    assert not bad_nested_report.exists()
