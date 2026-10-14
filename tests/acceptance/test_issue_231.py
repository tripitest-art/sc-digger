from pathlib import Path
import sqlite3
from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.harmonic import find_mix_candidates

def test_mix_tool_returns_compatible_tracks(tmp_path: Path):
    track_db = tmp_path / "tracks.sqlite"
    with TrackDB(track_db) as db:
        db.upsert_track(TrackRecord(
            path="/music/Schranz/track1.wav", mtime=1.0, size=1000,
            bpm=155.0, key_camelot="8A", status=TrackStatus.ARCHIVE
        ))
        db.upsert_track(TrackRecord(
            path="/music/Schranz/track2.wav", mtime=1.0, size=1000,
            bpm=155.0, key_camelot="9A", status=TrackStatus.ARCHIVE
        ))
        db.upsert_track(TrackRecord(
            path="/music/Schranz/incompatible.wav", mtime=1.0, size=1000,
            bpm=155.0, key_camelot="1A", status=TrackStatus.ARCHIVE
        ))
        res = find_mix_candidates(db, "8A", 155.0, bpm_tolerance=2.0)
        assert len(res) == 2
        paths = [r.path for r in res]
        assert "/music/Schranz/track1.wav" in paths
        assert "/music/Schranz/track2.wav" in paths
