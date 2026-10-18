"""Akzeptanztests: Sammlungs-Index in der Track-DB mit Dauer und schnellem Reload."""
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from sc_digger import main as m
from sc_digger.collection import Collection, open_collection
from sc_digger.audit import audit_file
from sc_digger.db import MIGRATIONS, TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from sc_digger.quality import check_file
from tests.test_modes import mk

ROOT = Path(__file__).resolve().parents[2]


def sc(i, title="Surrender", artist="Ueberrest", seconds=210):
    return mk(i, title=title, user={"username": artist, "permalink_url": "https://x"},
              duration=int(seconds * 1000), full_duration=int(seconds * 1000))


def _cfg(tmp_path, db_path, folder="gibt-es-nicht"):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["download"] = {**cfg.raw["download"], "collection_dir": str(tmp_path / folder)}
    cfg.raw["state"] = {**cfg.raw["state"], "track_db_path": str(db_path)}
    return cfg


def _archive(db, tmp_path, name="Ueberrest - Surrender.wav", duration=212.5, status=TrackStatus.ARCHIVE):
    return db.upsert_track(TrackRecord(
        path=str(tmp_path / "coll" / name), mtime=1.0, size=1000,
        duration=duration, status=status,
    ))


def test_migration_12_adds_duration_column(tmp_path):
    assert 12 in {version for version, _, _ in MIGRATIONS}
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        cols = {row["name"]: row["type"] for row in db.db.execute("PRAGMA table_info(tracks)").fetchall()}
        assert cols["duration"].upper() == "REAL"
        rec = _archive(db, tmp_path)
        assert db.get_track_by_path(rec.path).duration == 212.5


def test_list_collection_tracks_returns_archive_and_adopted_by_id(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        a = _archive(db, tmp_path, "1.wav", 200.0)
        _archive(db, tmp_path, "inbox.wav", 220.0, TrackStatus.INBOX)
        b = _archive(db, tmp_path, "2.wav", 210.0, "adopted")
        _archive(db, tmp_path, "rej.wav", 230.0, TrackStatus.REJECTED)
        assert [t.id for t in db.list_collection_tracks()] == [a.id, b.id]


def test_collection_loads_from_db_without_rglob_and_without_probe(tmp_path):
    calls = []

    def probe_fail(path):
        calls.append(path)
        raise RuntimeError("Darf nicht aufgerufen werden")

    with TrackDB(tmp_path / "tracks.sqlite") as db:
        _archive(db, tmp_path)
        coll = Collection(tmp_path / "gibt-es-nicht", db=db, duration_fn=probe_fail)
    assert len(coll.index) == 1
    assert coll.find_duplicate(sc(1, seconds=210)) == "Ueberrest - Surrender.wav"
    assert coll.find_duplicate(sc(2, seconds=390)) is None
    assert calls == []


def test_collection_falls_back_to_rglob_when_db_is_none_or_empty(tmp_path):
    folder = tmp_path / "collection"
    folder.mkdir()
    (folder / "Ueberrest - Surrender.wav").write_bytes(b"x")
    assert len(Collection(folder, db=None).index) == 1
    with TrackDB(tmp_path / "leer.sqlite") as db:
        coll = Collection(folder, db=db)
    assert coll.find_duplicate(sc(1, seconds=210)) == "Ueberrest - Surrender.wav"


def test_open_collection_uses_the_track_db_when_configured(tmp_path):
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        _archive(db, tmp_path)
    coll = open_collection(_cfg(tmp_path, db_path))
    assert len(coll.index) == 1


def test_open_collection_without_track_db_reads_the_folder(tmp_path):
    folder = tmp_path / "collection"
    folder.mkdir()
    (folder / "Ueberrest - Surrender.wav").write_bytes(b"x")
    coll = open_collection(_cfg(tmp_path, tmp_path / "gibt-es-nicht.sqlite", "collection"))
    assert len(coll.index) == 1


def test_process_finds_duplicates_through_the_db_index(tmp_path):
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        _archive(db, tmp_path)
    fresh, dupes = m.process([sc(1, seconds=210)], _cfg(tmp_path, db_path), dry_run=True)
    assert [t.id for t in dupes] == [1]
    assert fresh == []


def test_check_file_reports_the_duration(tmp_path):
    wav = tmp_path / "t.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                    str(wav)], check=True)
    report = check_file(wav, Config.load(ROOT / "config.yaml"))
    assert report["duration"] == pytest.approx(4.0, abs=0.2)


def test_audit_file_stores_the_duration_in_the_track_db(tmp_path):
    audio = tmp_path / "Ueberrest - Surrender.wav"
    audio.write_bytes(b"dummy audio")
    report = {"file": audio.name, "codec": "pcm_s16le", "ext": "wav", "bitrate_kbps": 1411,
              "sample_rate": 44100, "cutoff_hz": 21000, "ok": True, "reason": "", "duration": 215.4}
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        with patch("sc_digger.audit.check_file", return_value=report), \
             patch("sc_digger.audit.read_existing_tags", return_value={"bpm": 150.0, "key": "8A"}):
            audit_file(audio, Config.load(ROOT / "config.yaml"), db)
        assert db.get_track_by_path(audio).duration == 215.4
