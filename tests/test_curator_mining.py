"""Eigene Tests für Curator-Mining (Issue #45)."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from sc_digger import bot as b
from sc_digger import main as m
from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.soundcloud import SoundCloudClient, SoundCloudError


def _make_test_cfg(tmp_path) -> Config:
    cfg_path = Path(__file__).resolve().parents[1] / "config.yaml"
    cfg = Config.load(cfg_path)
    cfg.raw["state"] = {**cfg["state"], "track_db_path": str(tmp_path / "test_tracks.sqlite")}
    cfg.raw["curator_mining"] = {"min_appearances": 2, "max_likers_per_track": 50}
    cfg.raw["search"] = {
        **cfg["search"],
        "reference_accounts": ["https://soundcloud.com/existing_ref", "bare_ref"],
        "followed_users": ["https://soundcloud.com/existing_followed"],
    }
    return cfg


# ------------------------------------------------------------------ SoundCloudClient get_likers / get_reposters
def test_get_likers_and_reposters_success():
    sc = SoundCloudClient()
    fake_items = [
        {"id": 101, "permalink": "user1", "username": "User One"},
        {"user": {"id": 102, "permalink": "user2", "username": "User Two"}},
    ]
    with patch.object(sc, "_paginate", return_value=iter(fake_items)):
        likers = sc.get_likers(12345, max_results=10)
        assert likers == [
            {"id": 101, "permalink": "user1", "username": "User One"},
            {"id": 102, "permalink": "user2", "username": "User Two"},
        ]

    with patch.object(sc, "_paginate", return_value=iter(fake_items)):
        reposters = sc.get_reposters(12345, max_results=10)
        assert len(reposters) == 2
        assert reposters[0]["permalink"] == "user1"


def test_get_likers_api_error_returns_empty_list():
    sc = SoundCloudClient()

    def _failing_paginate(*args, **kwargs):
        raise SoundCloudError("404 Not Found")
        yield  # make it a generator

    with patch.object(sc, "_paginate", side_effect=_failing_paginate):
        res = sc.get_likers(999)
        assert res == []

    with patch.object(sc, "_paginate", side_effect=_failing_paginate):
        res = sc.get_reposters(999)
        assert res == []


def test_get_likers_zero_or_negative_max_results():
    sc = SoundCloudClient()
    assert sc.get_likers(123, max_results=0) == []
    assert sc.get_reposters(123, max_results=-5) == []


# ------------------------------------------------------------------ TrackDB get_liked_sc_ids
def test_get_liked_sc_ids_empty(tmp_path):
    with TrackDB(str(tmp_path / "empty.sqlite")) as db:
        assert db.get_liked_sc_ids() == []


def test_get_liked_sc_ids_filters_only_likes(tmp_path):
    with TrackDB(str(tmp_path / "feedback.sqlite")) as db:
        db.set_sc_feedback(10, "like")
        db.set_sc_feedback(20, "dislike")
        db.set_sc_feedback(30, "later")
        db.set_sc_feedback(40, "like")
        assert sorted(db.get_liked_sc_ids()) == [10, 40]


# ------------------------------------------------------------------ run_curator_mining
def test_curator_mining_zero_liked_tracks(tmp_path, capsys):
    cfg = _make_test_cfg(tmp_path)
    res = m.run_curator_mining(cfg, dry_run=True)
    assert res == []
    out = capsys.readouterr().out
    assert "Keine 👍-Tracks" in out


def test_curator_mining_api_returns_zero_likers(tmp_path):
    cfg = _make_test_cfg(tmp_path)
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        db.set_sc_feedback(1, "like")

    class EmptySC:
        def get_likers(self, track_id, max_results=50):
            return []
        def get_reposters(self, track_id, max_results=50):
            return []

    with patch.object(m, "_make_sc", return_value=EmptySC()):
        res = m.run_curator_mining(cfg, dry_run=True)
    assert res == []


def test_curator_mining_filtering_urls_and_bare_permalinks(tmp_path):
    cfg = _make_test_cfg(tmp_path)
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        db.set_sc_feedback(1, "like")
        db.set_sc_feedback(2, "like")

    class FilterSC:
        def get_likers(self, track_id, max_results=50):
            return [
                {"id": 1, "permalink": "existing_ref", "username": "Existing Ref"},
                {"id": 2, "permalink": "bare_ref", "username": "Bare Ref"},
                {"id": 3, "permalink": "existing_followed", "username": "Existing Followed"},
                {"id": 4, "permalink": "great_curator", "username": "Great Curator"},
            ]
        def get_reposters(self, track_id, max_results=50):
            return []

    with patch.object(m, "_make_sc", return_value=FilterSC()):
        candidates = m.run_curator_mining(cfg, dry_run=True)

    assert len(candidates) == 1
    user, count = candidates[0]
    assert user["permalink"] == "great_curator"
    assert count == 2


def test_curator_mining_telegram_call(tmp_path):
    cfg = _make_test_cfg(tmp_path)
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        db.set_sc_feedback(1, "like")

    class SingleSC:
        def get_likers(self, track_id, max_results=50):
            return [{"id": 42, "permalink": "dj_spotlight", "username": "DJ Spotlight"}]
        def get_reposters(self, track_id, max_results=50):
            return []

    calls = []
    with patch.object(m, "_make_sc", return_value=SingleSC()), \
         patch.object(m, "telegram_call", side_effect=lambda token, method, **kwargs: calls.append((method, kwargs))), \
         patch.object(Config, "telegram_token", new="dummy_token"), \
         patch.object(Config, "telegram_chat_id", new="123456"):
        candidates = m.run_curator_mining(cfg, dry_run=False, no_telegram=False)

    assert len(candidates) == 1
    assert len(calls) == 1
    method, kwargs = calls[0]
    assert method == "sendMessage"
    payload = kwargs["json"]
    assert payload["chat_id"] == "123456"
    assert "DJ Spotlight" in payload["text"]
    assert "dj_spotlight" in payload["text"]
    kb = payload["reply_markup"]["inline_keyboard"]
    assert kb[0][0]["callback_data"] == "curator_add:dj_spotlight"


# ------------------------------------------------------------------ bot handle_callback with curator_add
def test_handle_callback_curator_add(tmp_path):
    cfg = _make_test_cfg(tmp_path)
    cfg_file = tmp_path / "bot_config.yaml"
    cfg_file.write_text("search:\n  reference_accounts: []\n", encoding="utf-8")

    telegram_calls = []

    def fake_telegram_call(token, method, **kwargs):
        telegram_calls.append((method, kwargs))
        return {"ok": True}

    cq = {
        "id": "cq_123",
        "data": "curator_add:spotlight_curator",
        "message": {
            "message_id": 999,
            "chat": {"id": 123456},
        },
    }

    with patch.object(b, "telegram_call", side_effect=fake_telegram_call), \
         patch.object(Config, "telegram_chat_id", new="123456"):
        b.handle_callback(cfg, cq, config_path=cfg_file)

    raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    assert "spotlight_curator" in raw["search"]["reference_accounts"]

    assert any(
        method == "answerCallbackQuery" and "spotlight_curator" in kwargs.get("json", {}).get("text", "")
        for method, kwargs in telegram_calls
    )


def test_handle_callback_ignores_unauthorized_chat(tmp_path):
    cfg = _make_test_cfg(tmp_path)
    cfg_file = tmp_path / "bot_config.yaml"
    cfg_file.write_text("search:\n  reference_accounts: []\n", encoding="utf-8")

    telegram_calls = []
    cq = {
        "id": "cq_123",
        "data": "curator_add:bad_actor",
        "message": {"message_id": 999, "chat": {"id": 999999}},
    }

    with patch.object(b, "telegram_call", side_effect=lambda *a, **k: telegram_calls.append(a)), \
         patch.object(Config, "telegram_chat_id", new="123456"):
        b.handle_callback(cfg, cq, config_path=cfg_file)

    assert len(telegram_calls) == 0
    raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    assert raw["search"]["reference_accounts"] == []
