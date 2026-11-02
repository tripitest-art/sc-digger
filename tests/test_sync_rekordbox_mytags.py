"""Tests für scripts/sync_rekordbox_mytags.py."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from scripts.sync_rekordbox_mytags import (
    classify_bpm,
    classify_version,
    create_backup,
    is_rekordbox_running,
    sync_mytags,
)


def test_classify_bpm_buckets():
    """Prüft die Zuordnung aller BPM-Bereiche inklusive Oktavkorrektur."""
    assert classify_bpm(140.0) == "140-145"
    assert classify_bpm(144.9) == "140-145"
    assert classify_bpm(145.0) == "145-150"
    assert classify_bpm(150.0) == "150-155"
    assert classify_bpm(155.0) == "155-160"
    assert classify_bpm(160.0) == "160-165"
    assert classify_bpm(165.0) == "165-170"
    assert classify_bpm(170.0) == "165-170"

    # Oktavkorrektur (Halbzeit-Erkennung bei Schranz)
    assert classify_bpm(79.0) == "155-160"   # 79 * 2 = 158 -> 155-160
    assert classify_bpm(80.0) == "160-165"   # 80 * 2 = 160 -> 160-165

    # Randfälle
    assert classify_bpm(None) is None
    assert classify_bpm(0) is None
    assert classify_bpm(135.0) == "140-145"  # Untergrenzen-Fallback
    assert classify_bpm(175.0) == "165-170"  # Obergrenzen-Fallback


def test_classify_version():
    """Prüft die Erkennung der Versionstypen für Schranz."""
    assert classify_version("Track (Original Mix)") == "Originale"
    assert classify_version("Track (Schranz Edit)") == "Edits"
    assert classify_version("Track (Hard Rework)") == "Reworks"
    assert classify_version("Track (VIP Mix)") == "VIPs"
    assert classify_version("Track (Bootleg)") == "Bootlegs"
    assert classify_version("Track (Flip)") == "Flips"
    assert classify_version("Track (Remix)") == "Remixe"
    assert classify_version("Track (DJ Tool)") == "Tools"

    # Fallback auf Dateiname, wenn Titel unspezifisch
    assert classify_version("Track", filename="Artist - Track (Schranz Edit).wav") == "Edits"


def test_is_rekordbox_running():
    """Prüft die Prozessprüfung via psutil."""
    # Fall 1: Rekordbox läuft
    mock_proc1 = MagicMock()
    mock_proc1.info = {"name": "rekordbox.exe"}
    with patch("psutil.process_iter", return_value=[mock_proc1]):
        assert is_rekordbox_running() is True

    # Fall 2: Rekordbox läuft nicht
    mock_proc2 = MagicMock()
    mock_proc2.info = {"name": "chrome.exe"}
    with patch("psutil.process_iter", return_value=[mock_proc2]):
        assert is_rekordbox_running() is False


def test_create_backup(tmp_path: Path):
    """Prüft die Erstellung eines master.db Backups."""
    db_file = tmp_path / "master.db"
    db_file.write_text("dummy database content")

    backup = create_backup(db_file, backup_dir=tmp_path / "backups")
    assert backup.is_file()
    assert backup.read_text() == "dummy database content"
    assert "master.db.backup_" in backup.name


def test_safety_check_aborts_when_rekordbox_running(tmp_path: Path):
    """Prüft, dass der Live-Lauf abbricht, wenn Rekordbox läuft."""
    db_file = tmp_path / "master.db"
    db_file.write_text("dummy")

    with patch("scripts.sync_rekordbox_mytags.is_rekordbox_running", return_value=True):
        stats = sync_mytags(
            master_db_path=db_file,
            collection_dir=tmp_path,
            dry_run=False,
            force=False,
        )
        assert stats["status"] == "aborted"
        assert stats["reason"] == "rekordbox_running"
