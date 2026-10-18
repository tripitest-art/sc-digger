"""Akzeptanztests: Telegram Inline-Buttons für Curator-Mining (Issue #329)."""
from pathlib import Path
import pytest
import yaml

from sc_digger import bot
from sc_digger.models import Config

ROOT = Path(__file__).resolve().parents[2]


class FakeSC:
    def get_likers(self, sc_id, max_results=50):
        return [
            {"permalink": "techno_curator", "username": "Techno Curator"},
            {"permalink": "acid_dj", "username": "Acid DJ"},
        ]

    def get_reposters(self, sc_id, max_results=50):
        return [
            {"permalink": "techno_curator", "username": "Techno Curator"},
        ]


def test_curator_mining_callback_parser():
    assert bot.parse_curator_follow_callback("cf:techno_curator") == "techno_curator"
    assert bot.parse_curator_follow_callback("cf:sub/invalid") is not None
    assert bot.parse_curator_follow_callback("fb:like:12345") is None
    assert bot.parse_curator_follow_callback(None) is None
    assert bot.parse_curator_follow_callback("") is None


def test_curator_mining_keyboard_generation():
    candidates = [
        ("techno_curator", "Techno Curator", 2),
        ("acid_dj", "Acid DJ", 1),
    ]
    kb = bot.curator_mining_keyboard(candidates)
    assert kb is not None
    rows = kb.get("inline_keyboard", [])
    assert len(rows) == 2
    assert rows[0][0]["callback_data"] == "cf:techno_curator"
    assert "Techno Curator" in rows[0][0]["text"]
    assert rows[1][0]["callback_data"] == "cf:acid_dj"


def test_add_user_to_followed_config_persists_override(tmp_path: Path, monkeypatch):
    base_file = tmp_path / "config.yaml"
    base_file.write_text(yaml.safe_dump({
        "search": {"followed_users": ["https://soundcloud.com/existing_user"]},
        "state": {"track_db_path": str(tmp_path / "tracks.sqlite")},
    }), encoding="utf-8")
    local_file = tmp_path / "config.local.yaml"
    monkeypatch.setenv("SC_DIGGER_CONFIG_LOCAL", str(local_file))

    cfg = Config.load(base_file, local=local_file)

    # 1. Neuer User
    added = bot.add_user_to_followed_config(cfg, "techno_curator")
    assert added is True
    assert local_file.exists()

    # Nach Laden aus local_file muss der User drin sein
    cfg_reloaded = Config.load(base_file, local=local_file)
    followed = [str(u).lower() for u in cfg_reloaded.raw["search"]["followed_users"]]
    assert any("techno_curator" in u for u in followed)

    # 2. Bereits vorhandener User (idempotent, gibt False zurück)
    added_again = bot.add_user_to_followed_config(cfg_reloaded, "techno_curator")
    assert added_again is False


def test_handle_callback_curator_follow(tmp_path: Path, monkeypatch):
    base_file = tmp_path / "config.yaml"
    base_file.write_text(yaml.safe_dump({
        "search": {"followed_users": []},
        "state": {"track_db_path": str(tmp_path / "tracks.sqlite")},
    }), encoding="utf-8")
    local_file = tmp_path / "config.local.yaml"
    monkeypatch.setenv("SC_DIGGER_CONFIG_LOCAL", str(local_file))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")

    cfg = Config.load(base_file, local=local_file)

    telegram_calls = []

    def fake_telegram_call(token, method, **kwargs):
        telegram_calls.append((method, kwargs.get("json") or kwargs.get("params")))
        return {"ok": True, "result": True}

    monkeypatch.setattr(bot, "telegram_call", fake_telegram_call)

    kb = bot.curator_mining_keyboard([("techno_curator", "Techno Curator", 3)])
    cq = {
        "id": "cq_100",
        "data": "cf:techno_curator",
        "message": {
            "message_id": 42,
            "chat": {"id": 999},
            "reply_markup": kb,
        },
    }

    bot.handle_callback(cfg, cq)

    # Muss answerCallbackQuery aufgerufen haben
    answers = [c for c in telegram_calls if c[0] == "answerCallbackQuery"]
    assert len(answers) == 1
    assert answers[0][1]["callback_query_id"] == "cq_100"

    # Muss editMessageReplyMarkup aufgerufen haben mit markiertem Button
    edits = [c for c in telegram_calls if c[0] == "editMessageReplyMarkup"]
    assert len(edits) == 1
    new_btn = edits[0][1]["reply_markup"]["inline_keyboard"][0][0]
    assert "✅" in new_btn["text"]
