"""Akzeptanztests: Curator-Mining Sammlungs-Kandidaten (Issue #333)."""
from pathlib import Path
import pytest
import yaml

from sc_digger import bot
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config

ROOT = Path(__file__).resolve().parents[2]


class FakeSC:
    def get_likers(self, sc_id, max_results=50):
        return []

    def get_reposters(self, sc_id, max_results=50):
        return []


def test_get_top_collection_artists(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        # 3x Viper XXL (archive)
        for i in range(3):
            db.upsert_track(TrackRecord(path=f"audio/v{i}.wav", mtime=1.0, size=10, status="archive", artist="Viper XXL"))
        # 2x O.B.I. (archive) + 1x O.B.I. (inbox aber adopted_at gesetzt)
        db.upsert_track(TrackRecord(path="audio/o1.wav", mtime=1.0, size=10, status="archive", artist="O.B.I."))
        db.upsert_track(TrackRecord(path="audio/o2.wav", mtime=1.0, size=10, status="archive", artist="O.B.I."))
        db.upsert_track(TrackRecord(path="audio/o3.wav", mtime=1.0, size=10, status="inbox", adopted_at="2026-10-18 12:00:00", artist="O.B.I."))
        # 1x Einzelkünstler (unter Schwellwert)
        db.upsert_track(TrackRecord(path="audio/s1.wav", mtime=1.0, size=10, status="archive", artist="Single Artist"))

        top = db.get_top_collection_artists(min_tracks=3)
        names = [name for name, count in top]
        assert "Viper XXL" in names
        assert "O.B.I." in names
        assert "Single Artist" not in names


def test_run_curator_mining_includes_collection_candidates(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        for i in range(4):
            db.upsert_track(TrackRecord(path=f"audio/track_{i}.wav", mtime=1.0, size=10, status="archive", artist="Svetec"))

    cfg_data = {
        "search": {"followed_users": ["already_followed"], "reference_accounts": []},
        "state": {"track_db_path": str(db_path)},
        "curator_mining": {"min_collection_tracks": 3},
    }
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump(cfg_data), encoding="utf-8")
    cfg = Config.load(cfg_file)

    text = bot.run_curator_mining(FakeSC(), cfg)
    assert "Svetec" in text
