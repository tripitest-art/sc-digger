"""Tests für Curator-Mining: Liker/Reposter-Abfrage, DB-Abfragen und Bot-Befehl."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from sc_digger import bot
from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.soundcloud import SoundCloudClient, SoundCloudError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg(tmp_path):
    c = Config.load(ROOT / "config.yaml")
    c.raw["state"] = {**c["state"], "track_db_path": str(tmp_path / "tracks.sqlite")}
    c.raw["curator_mining"] = {"min_appearances": 2, "max_likers_per_track": 50}
    c.raw["search"] = {
        **c["search"],
        "reference_accounts": ["https://soundcloud.com/ref_dj_url", "bare_ref_dj"],
        "followed_users": ["followed_producer"],
    }
    return c


# ================================================================= SoundCloudClient tests

def test_get_likers_and_reposters_success():
    sc = SoundCloudClient()
    fake_collection = [
        {"id": 1, "permalink": "dj_alpha", "username": "DJ Alpha"},
        {"user": {"id": 2, "permalink": "dj_beta", "username": "DJ Beta"}},
    ]
    with patch.object(sc, "_paginate", return_value=iter(fake_collection)):
        likers = sc.get_likers(12345, max_results=10)
        assert len(likers) == 2
        assert likers[0] == {"id": 1, "permalink": "dj_alpha", "username": "DJ Alpha"}
        assert likers[1] == {"id": 2, "permalink": "dj_beta", "username": "DJ Beta"}

    with patch.object(sc, "_paginate", return_value=iter(fake_collection)):
        reposters = sc.get_reposters(12345, max_results=10)
        assert len(reposters) == 2
        assert reposters[0] == {"id": 1, "permalink": "dj_alpha", "username": "DJ Alpha"}
        assert reposters[1] == {"id": 2, "permalink": "dj_beta", "username": "DJ Beta"}


def test_get_likers_and_reposters_api_error_returns_empty_list():
    sc = SoundCloudClient()

    def boom(*args, **kwargs):
        raise SoundCloudError("API down")

    with patch.object(sc, "_paginate", side_effect=boom):
        assert sc.get_likers(12345) == []
        assert sc.get_reposters(12345) == []

    def request_boom(*args, **kwargs):
        raise requests.RequestException("Network failed")

    with patch.object(sc, "_paginate", side_effect=request_boom):
        assert sc.get_likers(12345) == []
        assert sc.get_reposters(12345) == []


def test_get_likers_max_results_zero_or_negative():
    sc = SoundCloudClient()
    assert sc.get_likers(12345, max_results=0) == []
    assert sc.get_likers(12345, max_results=-5) == []
    assert sc.get_reposters(12345, max_results=0) == []
    assert sc.get_reposters(12345, max_results=-5) == []


# ================================================================= DB tests

def test_get_liked_sc_ids_empty(tmp_path):
    with TrackDB(str(tmp_path / "t.sqlite")) as db:
        assert db.get_liked_sc_ids() == []


def test_get_liked_sc_ids_status_change(tmp_path):
    with TrackDB(str(tmp_path / "t.sqlite")) as db:
        db.set_sc_feedback(100, "like", "https://soundcloud.com/t/100")
        db.set_sc_feedback(200, "later", "https://soundcloud.com/t/200")
        assert db.get_liked_sc_ids() == [100]

        # Status auf dislike ändern -> nicht mehr in liked
        db.set_sc_feedback(100, "dislike")
        assert db.get_liked_sc_ids() == []


# ================================================================= run_curator_mining tests

def test_curator_mining_combines_likers_and_reposters(cfg):
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        db.set_sc_feedback(101, "like")
        db.set_sc_feedback(102, "like")

    class FakeSC:
        def get_likers(self, track_id, max_results=50):
            if track_id == 101:
                return [{"id": 1, "permalink": "dj_repost_and_like", "username": "Combo DJ"}]
            return []

        def get_reposters(self, track_id, max_results=50):
            if track_id == 102:
                return [{"id": 1, "permalink": "dj_repost_and_like", "username": "Combo DJ"}]
            return []

    text = bot.run_curator_mining(FakeSC(), cfg)
    assert "Combo DJ" in text
    assert "soundcloud.com/dj_repost_and_like" in text
    assert "2× gesehen" in text


def test_curator_mining_sorts_by_appearances_descending(cfg):
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        for tid in [1, 2, 3]:
            db.set_sc_feedback(tid, "like")

    class FakeSC:
        def get_likers(self, track_id, max_results=50):
            # top_curator: 3x, regular_curator: 2x
            if track_id == 1:
                return [
                    {"id": 1, "permalink": "top_curator", "username": "Top Curator"},
                    {"id": 2, "permalink": "regular_curator", "username": "Regular Curator"},
                ]
            elif track_id == 2:
                return [
                    {"id": 1, "permalink": "top_curator", "username": "Top Curator"},
                    {"id": 2, "permalink": "regular_curator", "username": "Regular Curator"},
                ]
            else:
                return [
                    {"id": 1, "permalink": "top_curator", "username": "Top Curator"},
                ]

        def get_reposters(self, track_id, max_results=50):
            return []

    text = bot.run_curator_mining(FakeSC(), cfg)
    lines = text.strip().split("\n")
    top_pos = next(i for i, l in enumerate(lines) if "Top Curator" in l)
    reg_pos = next(i for i, l in enumerate(lines) if "Regular Curator" in l)
    assert top_pos < reg_pos
    assert "3× gesehen" in lines[top_pos]
    assert "2× gesehen" in lines[reg_pos]


def test_curator_mining_missing_username_falls_back_to_permalink(cfg):
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        db.set_sc_feedback(1, "like")
        db.set_sc_feedback(2, "like")

    class FakeSC:
        def get_likers(self, track_id, max_results=50):
            return [{"id": 5, "permalink": "noname_dj", "username": None}]

        def get_reposters(self, track_id, max_results=50):
            return []

    text = bot.run_curator_mining(FakeSC(), cfg)
    assert "noname_dj (soundcloud.com/noname_dj) – 2× gesehen" in text


def test_curator_mining_no_track_db_path():
    empty_cfg = Config({"state": {}})
    sc = MagicMock()
    text = bot.run_curator_mining(sc, empty_cfg)
    assert text == "Keine neuen Curator-Vorschläge."


def test_curator_mining_all_filtered_returns_empty_notice(cfg):
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        db.set_sc_feedback(1, "like")
        db.set_sc_feedback(2, "like")

    class FakeSC:
        def get_likers(self, track_id, max_results=50):
            # ref_dj_url and followed_producer are in search configuration
            return [
                {"id": 1, "permalink": "ref_dj_url", "username": "Ref DJ"},
                {"id": 2, "permalink": "followed_producer", "username": "Followed"},
            ]

        def get_reposters(self, track_id, max_results=50):
            return []

    text = bot.run_curator_mining(FakeSC(), cfg)
    assert text == "Keine neuen Curator-Vorschläge."


# ================================================================= Bot handler tests

def test_handle_message_curator_mining_command(cfg, monkeypatch):
    calls = []

    def fake_telegram_call(token, method, **kw):
        calls.append((method, kw.get("json")))
        return {"ok": True, "result": True}

    monkeypatch.setattr(bot, "telegram_call", fake_telegram_call)
    monkeypatch.setattr(bot, "run_curator_mining", lambda sc, c: "🔍 Curator-Vorschläge\n\n• Test")

    sc = MagicMock()

    # /curator-mining
    bot.handle_message(cfg, sc, "12345", "/curator-mining")
    assert len(calls) == 1
    assert calls[0][0] == "sendMessage"
    assert calls[0][1]["chat_id"] == "12345"
    assert "🔍 Curator-Vorschläge" in calls[0][1]["text"]

    # /curator-mining@botname
    calls.clear()
    bot.handle_message(cfg, sc, "12345", "/curator-mining@sc_digger_bot")
    assert len(calls) == 1
    assert "🔍 Curator-Vorschläge" in calls[0][1]["text"]

    # /curator_mining (Underscore-Variante)
    calls.clear()
    bot.handle_message(cfg, sc, "12345", "/curator_mining")
    assert len(calls) == 1
    assert "🔍 Curator-Vorschläge" in calls[0][1]["text"]
