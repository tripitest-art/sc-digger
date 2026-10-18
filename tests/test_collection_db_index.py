"""Sammlungs-Index aus der Track-DB: Rückfall, Reload, unbekannte Dauer, Sonderzeichen."""
import logging
from pathlib import Path

from sc_digger.collection import Collection, open_collection
from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from tests.test_modes import mk

ROOT = Path(__file__).resolve().parents[1]


def sc(i, title="Surrender", artist="Ueberrest", seconds=210):
    return mk(i, title=title, user={"username": artist, "permalink_url": "https://x"},
              duration=int(seconds * 1000), full_duration=int(seconds * 1000))


def _cfg(tmp_path, folder, db_path=None):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["download"] = {**cfg.raw["download"], "collection_dir": str(tmp_path / folder)}
    cfg.raw["state"] = {**cfg.raw["state"], "track_db_path": str(db_path)} if db_path else {}
    return cfg


def _archive(db, path, duration=None, status=TrackStatus.ARCHIVE):
    return db.upsert_track(TrackRecord(path=str(path), mtime=1.0, size=1000,
                                       duration=duration, status=status))


def test_reload_builds_the_index_from_the_db_again(tmp_path):
    folder = tmp_path / "collection"
    folder.mkdir()
    (folder / "Ueberrest - Surrender.wav").write_bytes(b"x")
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        coll = Collection(folder, db=db)
        assert coll.find_duplicate(sc(1, seconds=210)) == "Ueberrest - Surrender.wav"

        _archive(db, tmp_path / "coll" / "Other - Track.wav", 200.0)
        coll.reload()

    assert len(coll.index) == 1
    assert coll.find_duplicate(sc(2, title="Track", artist="Other", seconds=200)) == "Other - Track.wav"


def test_entry_without_duration_uses_duration_fn_on_name_match(tmp_path):
    calls = []

    def duration_fn(path):
        calls.append(path)
        return 210.0

    with TrackDB(tmp_path / "tracks.sqlite") as db:
        _archive(db, tmp_path / "coll" / "Ueberrest - Surrender.wav", duration=None)
        coll = Collection(tmp_path / "gibt-es-nicht", db=db, duration_fn=duration_fn)
        assert coll.find_duplicate(sc(1, seconds=210)) == "Ueberrest - Surrender.wav"

    assert [p.name for p in calls] == ["Ueberrest - Surrender.wav"]


def test_db_duration_avoids_duration_fn(tmp_path):
    def duration_fn(path):
        raise AssertionError("Dauer steht in der DB")

    with TrackDB(tmp_path / "tracks.sqlite") as db:
        _archive(db, tmp_path / "coll" / "Ueberrest - Surrender.wav", duration=200.0)
        coll = Collection(tmp_path / "gibt-es-nicht", db=db, duration_fn=duration_fn)
        # 210 s gegen 200 s: außerhalb der Toleranz -> kein Treffer, ohne probe
        assert coll.find_duplicate(sc(1, seconds=210)) is None


def test_broken_db_file_warns_and_falls_back_to_filesystem(tmp_path, caplog):
    folder = tmp_path / "collection"
    folder.mkdir()
    (folder / "Ueberrest - Surrender.wav").write_bytes(b"x")
    broken = tmp_path / "broken.sqlite"
    broken.write_bytes(b"das ist keine sqlite-Datei")

    with caplog.at_level(logging.WARNING):
        coll = open_collection(_cfg(tmp_path, "collection", broken))

    assert len(coll.index) == 1
    assert "Track-DB nicht lesbar" in caplog.text


def test_open_collection_without_state_section_reads_the_folder(tmp_path):
    folder = tmp_path / "collection"
    folder.mkdir()
    (folder / "Ueberrest - Surrender.wav").write_bytes(b"x")
    cfg = _cfg(tmp_path, "collection")
    cfg.raw.pop("state", None)

    coll = open_collection(cfg)

    assert coll.find_duplicate(sc(1, seconds=210)) == "Ueberrest - Surrender.wav"


def test_paths_with_special_characters_match_from_the_db(tmp_path):
    name = "Überrest & Söhne – Surrender (Original Mix).wav"
    path = tmp_path / "Sammlung ÄÖÜ" / name

    def no_probe(p):
        raise AssertionError("Dauer steht in der DB")

    with TrackDB(tmp_path / "tracks.sqlite") as db:
        _archive(db, path, duration=212.5)
        coll = Collection(tmp_path / "gibt-es-nicht", db=db, duration_fn=no_probe)
        assert coll.find_duplicate(sc(1, title="Surrender (Original Mix)", seconds=210)) == name
