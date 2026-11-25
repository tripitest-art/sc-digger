"""Eigene Tests: Artist/Titel aus Tags oder Dateinamen (Issue #702)."""
from __future__ import annotations

from pathlib import Path

import pytest

from sc_digger.audit import names_for_file, names_from_filename, read_existing_tags
from sc_digger.backfill import backfill_names
from sc_digger.db import TrackDB, TrackRecord, TrackStatus


@pytest.mark.parametrize("stem, erwartet", [
    ("007 Agent - Bond", ("Agent", "Bond")),
    ("12. Foo_-_Bar", (None, "Foo_-_Bar")),
    ("1_ Foo - Bar", ("1_ Foo", "Bar")),
    ("12. - Foo - Bar", ("Foo", "Bar")),
    ("Nur Artist - ", (None, "Nur Artist -")),
])
def test_names_from_filename_raender(stem, erwartet):
    assert names_from_filename(stem) == erwartet


def test_kaputte_wav_wirft_nicht(tmp_path):
    p = tmp_path / "A - B.wav"
    p.write_bytes(b"kein wav")
    assert read_existing_tags(p)["artist"] is None
    assert names_for_file(p) == ("A", "B")


def test_set_names_ueberschreibt_nichts_und_meldet_fehlende_zeile(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        rec = db.upsert_track(TrackRecord(path=str(tmp_path / "x.wav"), mtime=0.0, size=0,
                                          status=TrackStatus.ARCHIVE, artist="Alt"))
        assert db.set_names(rec.id, "Neu", "Titel") is True
        got = db.get_track_by_path(tmp_path / "x.wav")
        assert (got.artist, got.title) == ("Alt", "Titel")
        assert db.set_names(9999, "A", "B") is False


def test_tracks_without_artist_limit_und_leerer_string(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        for i, a in enumerate([None, "  ", "Da"]):
            db.upsert_track(TrackRecord(path=str(tmp_path / f"{i}.wav"), mtime=0.0, size=0,
                                        status=TrackStatus.ARCHIVE, artist=a))
        assert len(db.tracks_without_artist()) == 2
        assert len(db.tracks_without_artist(1)) == 1


def test_backfill_names_aendert_die_datei_nicht(tmp_path):
    datei = tmp_path / "Foo - Bar.wav"
    datei.write_bytes(b"RIFFxxxx")
    vorher = (datei.read_bytes(), datei.stat().st_mtime_ns)
    with TrackDB(tmp_path / "t.sqlite") as db:
        db.upsert_track(TrackRecord(path=str(datei), mtime=0.0, size=0, status=TrackStatus.ARCHIVE))
        assert backfill_names(db)["set"] == 1
    assert (datei.read_bytes(), datei.stat().st_mtime_ns) == vorher
