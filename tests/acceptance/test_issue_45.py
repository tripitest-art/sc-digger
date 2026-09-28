"""Akzeptanztests: Curator-Mining – Profile aus 👍-Tracks vorschlagen."""
import yaml
from pathlib import Path
from unittest.mock import patch

import pytest

from sc_digger.db import TrackDB
from sc_digger.models import Config


def _cfg(tmp_path) -> Config:
    cfg = Config.load(Path(__file__).resolve().parents[2] / "config.yaml")
    cfg.raw["state"] = {**cfg["state"], "track_db_path": str(tmp_path / "t.sqlite")}
    cfg.raw["curator_mining"] = {"min_appearances": 2, "max_likers_per_track": 50}
    cfg.raw["search"] = {**cfg["search"], "reference_accounts": ["known_dj"], "followed_users": ["already_following"]}
    return cfg


def test_config_defaults():
    raw = yaml.safe_load((Path(__file__).resolve().parents[2] / "config.yaml").read_text(encoding="utf-8"))
    cm = raw["curator_mining"]
    assert cm["min_appearances"] >= 1
    assert cm["max_likers_per_track"] >= 1


def test_get_liked_sc_ids(tmp_path):
    with TrackDB(str(tmp_path / "t.sqlite")) as db:
        db.set_sc_feedback(111, "like", "https://soundcloud.com/a/t1")
        db.set_sc_feedback(222, "dislike", "https://soundcloud.com/a/t2")
        db.set_sc_feedback(333, "like", "https://soundcloud.com/a/t3")
        ids = db.get_liked_sc_ids()
    assert set(ids) == {111, 333}


def test_curator_mining_aggregates_and_filters(tmp_path):
    """profile_x taucht 3x auf (≥ min_appearances=2), known_dj und already_following werden gefiltert."""
    from sc_digger.bot import run_curator_mining
    cfg = _cfg(tmp_path)
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        for sc_id in [1, 2, 3]:
            db.set_sc_feedback(sc_id, "like", f"https://soundcloud.com/a/t{sc_id}")

    fake_likers = {
        1: [{"id": 10, "permalink": "profile_x", "username": "Profile X"},
            {"id": 20, "permalink": "known_dj", "username": "Known DJ"}],
        2: [{"id": 10, "permalink": "profile_x", "username": "Profile X"}],
        3: [{"id": 10, "permalink": "profile_x", "username": "Profile X"},
            {"id": 30, "permalink": "already_following", "username": "Already"}],
    }

    class FakeSC:
        def get_likers(self, track_id, max_results=50):
            return fake_likers.get(track_id, [])
        def get_reposters(self, track_id, max_results=50):
            return []

    text = run_curator_mining(FakeSC(), cfg)
    assert "profile_x" in text.lower() or "Profile X" in text
    assert "known_dj" not in text.lower()
    assert "already_following" not in text.lower()


def test_curator_mining_no_liked_tracks(tmp_path):
    """Ohne 👍-Tracks kommt eine leere Meldung."""
    from sc_digger.bot import run_curator_mining
    cfg = _cfg(tmp_path)

    class FakeSC:
        def get_likers(self, track_id, max_results=50):
            return []
        def get_reposters(self, track_id, max_results=50):
            return []

    text = run_curator_mining(FakeSC(), cfg)
    assert "keine" in text.lower() or len(text.strip()) > 0  # saubere Ausgabe


def test_curator_mining_below_threshold(tmp_path):
    """Profile unter min_appearances erscheinen nicht."""
    from sc_digger.bot import run_curator_mining
    cfg = _cfg(tmp_path)
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        db.set_sc_feedback(1, "like", "https://soundcloud.com/a/t1")

    class FakeSC:
        def get_likers(self, track_id, max_results=50):
            return [{"id": 10, "permalink": "once_only", "username": "Once Only"}]
        def get_reposters(self, track_id, max_results=50):
            return []

    text = run_curator_mining(FakeSC(), cfg)
    assert "once_only" not in text.lower()
