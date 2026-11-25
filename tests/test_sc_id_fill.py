"""Eigene Tests zu Issue #749: sc_id aus dem Dateinamen-Präfix nachtragen."""
from pathlib import Path

from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.embeddings import backfill_embedding_jobs


def _row(db: TrackDB, path: Path, sc_id: int | None = None) -> TrackRecord:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return db.upsert_track(
        TrackRecord(path=str(path), mtime=1.0, size=1, status=TrackStatus.INBOX, sc_id=sc_id)
    )


def test_zeile_eines_anderen_profils_bleibt_unberuehrt(tmp_path):
    pfad = tmp_path / "tracks.sqlite"
    with TrackDB(pfad, profile_id="house") as house:
        fremd = _row(house, tmp_path / "inbox" / "[555] House - Track.wav")
    with TrackDB(pfad) as db:
        eigen = _row(db, tmp_path / "inbox" / "[556] Schranz - Track.wav")
        assert db.fill_sc_ids_from_paths() == 1
        assert db.get_track_by_id(fremd.id).sc_id is None
        assert db.get_track_by_id(eigen.id).sc_id == 556
    with TrackDB(pfad, profile_id="house") as house:
        # Das andere Profil holt seine Zeile selbst nach.
        assert house.fill_sc_ids_from_paths() == 1
        assert house.get_track_by_id(fremd.id).sc_id == 555


def test_praefix_ohne_ziffern_wird_nicht_veraendert(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        a = _row(db, tmp_path / "inbox" / "[abc] Artist - Titel.wav")
        b = _row(db, tmp_path / "inbox" / "Artist - Titel [123].wav")  # nicht am Anfang
        assert db.fill_sc_ids_from_paths() == 0
        assert db.get_track_by_id(a.id).sc_id is None
        assert db.get_track_by_id(b.id).sc_id is None


def test_updated_at_wird_gesetzt(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        a = _row(db, tmp_path / "inbox" / "[777] Artist - Titel.wav")
        db.db.execute("UPDATE tracks SET updated_at = '2000-01-01 00:00:00' WHERE id = ?", (a.id,))
        db.db.commit()
        assert db.fill_sc_ids_from_paths() == 1
        assert db.get_track_by_id(a.id).updated_at != "2000-01-01 00:00:00"


def test_backfill_ohne_labeled_only_fuellt_ebenfalls(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        a = _row(db, tmp_path / "inbox" / "[321] Unbewertet.wav")
        summary = backfill_embedding_jobs(db, labeled_only=False)
        assert summary.sc_id_filled == 1
        assert summary.queued == 1
        assert db.get_track_by_id(a.id).sc_id == 321


def test_backfill_ohne_nachzutragende_ids_meldet_null(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        _row(db, tmp_path / "inbox" / "[1] Schon da.wav", sc_id=1)
        _row(db, tmp_path / "inbox" / "Ohne Praefix.wav")
        assert backfill_embedding_jobs(db).sc_id_filled == 0
