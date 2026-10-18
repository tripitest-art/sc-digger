"""Tests für die Inline-Buttons des Curator-Minings (Issue #329)."""
from pathlib import Path

import pytest
import yaml

from sc_digger import bot
from sc_digger.db import TrackDB
from sc_digger.models import Config

ROOT = Path(__file__).resolve().parents[1]


def _cfg(tmp_path: Path, monkeypatch, *, followed=None, references=None,
         local_name="config.local.yaml") -> tuple[Config, Path]:
    base_file = tmp_path / "config.yaml"
    base_file.write_text(yaml.safe_dump({
        "search": {
            "followed_users": list(followed or []),
            "reference_accounts": list(references or []),
        },
        "state": {"track_db_path": str(tmp_path / "tracks.sqlite")},
    }), encoding="utf-8")
    local_file = tmp_path / local_name
    monkeypatch.setenv("SC_DIGGER_CONFIG_LOCAL", str(local_file))
    return Config.load(base_file, local=local_file), local_file


def _fake_telegram(monkeypatch) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []

    def fake_telegram_call(token, method, **kwargs):
        calls.append((method, kwargs.get("json") or kwargs.get("params") or {}))
        return {"ok": True, "result": True}

    monkeypatch.setattr(bot, "telegram_call", fake_telegram_call)
    return calls


# ============================================================ Parsen und Normalisieren

def test_callback_data_normalisiert_url_und_grossschreibung():
    assert bot.curator_follow_callback_data(
        " https://soundcloud.com/Techno_Curator/ ") == "cf:techno_curator"
    assert bot.curator_follow_callback_data("techno_curator") == "cf:techno_curator"


def test_callback_data_lehnt_leere_und_zu_lange_permalinks_ab():
    with pytest.raises(ValueError):
        bot.curator_follow_callback_data("")
    with pytest.raises(ValueError):
        bot.curator_follow_callback_data("a" * 61)


def test_parse_ignoriert_fremde_und_kaputte_daten():
    assert bot.parse_curator_follow_callback("cf:Techno_Curator") == "techno_curator"
    assert bot.parse_curator_follow_callback("cf:") is None
    assert bot.parse_curator_follow_callback("fb:like:12345") is None
    assert bot.parse_curator_follow_callback(None) is None
    assert bot.parse_curator_follow_callback(123) is None
    assert bot.parse_curator_follow_callback("cf:" + "a" * 61) is None


# ============================================================ Keyboard

def test_keyboard_ist_bei_leerer_liste_none():
    assert bot.curator_mining_keyboard([]) is None


def test_keyboard_ein_button_pro_zeile_mit_slug_callback():
    kb = bot.curator_mining_keyboard([
        ("https://soundcloud.com/Techno_Curator", "Techno Curator", 2),
        ("acid_dj", "", 1),
    ])
    assert kb == {"inline_keyboard": [
        [{"text": "➕ Techno Curator", "callback_data": "cf:techno_curator"}],
        [{"text": "➕ acid_dj", "callback_data": "cf:acid_dj"}],
    ]}


# ============================================================ Eintragen in die Override-Datei

def test_add_user_normalisiert_url_und_ist_idempotent(tmp_path, monkeypatch):
    cfg, local_file = _cfg(tmp_path, monkeypatch)

    assert bot.add_user_to_followed_config(
        cfg, "https://soundcloud.com/Techno_Curator/") is True
    assert local_file.is_file()
    saved = yaml.safe_load(local_file.read_text(encoding="utf-8"))
    assert saved["search"]["followed_users"] == ["https://soundcloud.com/techno_curator"]

    reloaded = Config.load(tmp_path / "config.yaml", local=local_file)
    assert bot.add_user_to_followed_config(reloaded, "techno_curator") is False
    saved_again = yaml.safe_load(local_file.read_text(encoding="utf-8"))
    assert saved_again["search"]["followed_users"] == ["https://soundcloud.com/techno_curator"]


def test_add_user_erkennt_reference_accounts(tmp_path, monkeypatch):
    cfg, local_file = _cfg(tmp_path, monkeypatch,
                           references=["https://soundcloud.com/bare_ref_dj"])

    assert bot.add_user_to_followed_config(cfg, "bare_ref_dj") is False
    assert not local_file.exists()


def test_add_user_behaelt_andere_lokale_schluessel(tmp_path, monkeypatch):
    cfg, local_file = _cfg(tmp_path, monkeypatch)
    local_file.write_text(yaml.safe_dump({
        "search": {"followed_users": [], "bpm_min": 150},
    }), encoding="utf-8")

    assert bot.add_user_to_followed_config(cfg, "acid_dj") is True
    saved = yaml.safe_load(local_file.read_text(encoding="utf-8"))
    assert saved["search"]["bpm_min"] == 150
    assert "https://soundcloud.com/acid_dj" in saved["search"]["followed_users"]


def test_add_user_ohne_override_pfad_gibt_false(tmp_path, monkeypatch):
    cfg, _local_file = _cfg(tmp_path, monkeypatch)
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)

    assert bot.add_user_to_followed_config(cfg, "techno_curator") is False


def test_add_user_wirft_nie_bei_unbeschreibbarem_pfad(tmp_path, monkeypatch):
    cfg, _local_file = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("SC_DIGGER_CONFIG_LOCAL",
                       str(tmp_path / "fehlt" / "config.local.yaml"))

    assert bot.add_user_to_followed_config(cfg, "techno_curator") is False


# ============================================================ Callback-Verarbeitung

def _follow_callback(chat_id: int = 999, data: str = "cf:techno_curator",
                     cq_id: str = "cq_100") -> dict:
    return {
        "id": cq_id,
        "data": data,
        "message": {
            "message_id": 42,
            "chat": {"id": chat_id},
            "reply_markup": bot.curator_mining_keyboard([
                ("techno_curator", "Techno Curator", 2),
                ("acid_dj", "Acid DJ", 1),
            ]),
        },
    }


def test_callback_aus_fremdem_chat_wird_ignoriert(tmp_path, monkeypatch):
    cfg, local_file = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    calls = _fake_telegram(monkeypatch)

    bot.handle_callback(cfg, _follow_callback(chat_id=111))

    assert calls == []
    assert not local_file.exists()


def test_doppelter_klick_bleibt_idempotent(tmp_path, monkeypatch):
    cfg, local_file = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    calls = _fake_telegram(monkeypatch)

    bot.handle_callback(cfg, _follow_callback(cq_id="cq_1"))
    bot.handle_callback(cfg, _follow_callback(cq_id="cq_2"))

    answers = [c[1]["text"] for c in calls if c[0] == "answerCallbackQuery"]
    assert "hinzugefügt" in answers[0]
    assert "bereits" in answers[1]

    saved = yaml.safe_load(local_file.read_text(encoding="utf-8"))
    assert saved["search"]["followed_users"] == ["https://soundcloud.com/techno_curator"]


def test_klick_deaktiviert_nur_den_eigenen_button(tmp_path, monkeypatch):
    cfg, _local_file = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    calls = _fake_telegram(monkeypatch)

    bot.handle_callback(cfg, _follow_callback())

    edits = [c[1] for c in calls if c[0] == "editMessageReplyMarkup"]
    rows = edits[0]["reply_markup"]["inline_keyboard"]
    assert rows[0][0]["text"] == "✅ Techno Curator"
    assert "callback_data" not in rows[0][0]
    assert rows[0][0]["url"] == "https://soundcloud.com/techno_curator"
    assert rows[1][0]["callback_data"] == "cf:acid_dj"


def test_kaputte_curator_callback_data_wird_als_unbekannt_beantwortet(tmp_path, monkeypatch):
    cfg, _local_file = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    calls = _fake_telegram(monkeypatch)

    bot.handle_callback(cfg, _follow_callback(data="cf:"))

    answers = [c[1]["text"] for c in calls if c[0] == "answerCallbackQuery"]
    assert answers == ["Unbekannte Aktion"]
    assert not [c for c in calls if c[0] == "editMessageReplyMarkup"]


# ============================================================ /curator_mining-Befehl

def test_curator_mining_befehl_sendet_buttons(tmp_path, monkeypatch):
    cfg, _local_file = _cfg(tmp_path, monkeypatch)
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        db.set_sc_feedback(1, "like")
        db.set_sc_feedback(2, "like")

    class FakeSC:
        def get_likers(self, sc_id, max_results=50):
            return [{"permalink": "techno_curator", "username": "Techno Curator"}]

        def get_reposters(self, sc_id, max_results=50):
            return []

    calls = _fake_telegram(monkeypatch)
    bot.handle_message(cfg, FakeSC(), "999", "/curator_mining")

    assert calls[0][0] == "sendMessage"
    kb = calls[0][1]["reply_markup"]
    assert kb["inline_keyboard"] == [
        [{"text": "➕ Techno Curator", "callback_data": "cf:techno_curator"}],
    ]
