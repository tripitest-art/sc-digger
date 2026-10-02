"""Akzeptanztests: Bot-Befehl /preview – Voice-Nachricht aus heruntergeladenem Track (Teil 2 von #53)."""
from pathlib import Path

import pytest

from sc_digger import bot, output
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    c = Config.load(ROOT / "config.yaml")
    c.raw["state"] = {**c["state"], "track_db_path": str(tmp_path / "tracks.sqlite")}
    return c


@pytest.fixture
def text_calls(monkeypatch):
    seen = []

    def fake(token, method, **kw):
        seen.append((method, kw.get("json")))
        return {"ok": True, "result": True}

    monkeypatch.setattr(bot, "telegram_call", fake)
    return seen


@pytest.fixture
def voice_calls(monkeypatch):
    """Ersetzt output.send_telegram_voice (nicht telegram_call).
    Prüft, dass voice_path beim Aufruf existiert – die Datei wird erst danach
    von preview_reply gelöscht."""
    seen = []

    def fake(cfg_obj, voice_path, caption=None, *, chat_id=None):
        assert Path(voice_path).exists(), "Voice-Datei fehlt beim Versand"
        seen.append({"caption": caption, "chat_id": chat_id,
                     "path": Path(voice_path)})

    monkeypatch.setattr(output, "send_telegram_voice", fake)
    return seen


@pytest.fixture
def fake_preview(monkeypatch):
    """Ersetzt extract_preview: schreibt eine Stumpf-OGG und meldet Erfolg.
    fail=True: schreibt die Datei trotzdem und gibt dann False zurück –
    so ist prüfbar, dass preview_reply auch im Fehlerfall aufräumt."""
    state = {"fail": False, "start_s": "not-called", "out_path": None}

    def fake(input_path, output_path, *, start_s=None, duration_s=20.0,
             bitrate_kbps=64):
        state["start_s"] = start_s
        state["out_path"] = Path(output_path)
        Path(output_path).write_bytes(b"OggS-fake")
        return not state["fail"]

    monkeypatch.setattr(bot.preview, "extract_preview", fake)
    return state


def _track(tmp_path, cfg, *, path=None, artist="Svetec", title="Raw", create_file=True):
    """Legt einen Track in der Track-DB an. create_file=False: Eintrag ohne Datei."""
    audio = path if path is not None else tmp_path / f"{artist} - {title}.mp3"
    if create_file:
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"fake-audio")
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        return db.upsert_track(TrackRecord(
            path=str(audio), mtime=1.0, size=10, artist=artist, title=title))


def test_preview_by_searchtext_sends_voice_with_caption(cfg, voice_calls, fake_preview, tmp_path):
    _track(tmp_path, cfg)

    result = bot.preview_reply(cfg, "/preview svetec raw", chat_id="42")

    assert result is None
    assert fake_preview["start_s"] is None
    assert len(voice_calls) == 1
    call = voice_calls[0]
    assert call["caption"] == "Svetec – Raw"
    assert call["chat_id"] == "42"
    assert call["path"].name.endswith(".ogg")


def test_preview_pure_digits_uses_local_track_id(cfg, voice_calls, fake_preview, tmp_path):
    rec = _track(tmp_path, cfg)

    result = bot.preview_reply(cfg, f"/preview {rec.id}", chat_id="42")

    assert result is None
    assert len(voice_calls) == 1
    assert voice_calls[0]["caption"] == "Svetec – Raw"


def test_preview_missing_argument_returns_usage_without_sending(cfg, text_calls, voice_calls, fake_preview):
    assert bot.preview_reply(cfg, "/preview") == bot.PREVIEW_USAGE
    assert text_calls == []  # preview_reply sendet selbst keine Textnachrichten
    assert voice_calls == []


def test_preview_no_match_answers_clearly(cfg, text_calls, voice_calls, fake_preview):
    reply = bot.preview_reply(cfg, "/preview gibtsnicht")

    assert reply is not None
    assert "gibtsnicht" in reply
    assert voice_calls == []


def test_preview_search_finds_exactly_one_case_insensitive(cfg, voice_calls, fake_preview, tmp_path):
    _track(tmp_path, cfg, artist="Svetec", title="Raw")
    _track(tmp_path, cfg, artist="Arkus P.", title="Weißes Rauschen")

    result = bot.preview_reply(cfg, "/preview SVETEC", chat_id="42")

    assert result is None
    assert len(voice_calls) == 1
    assert voice_calls[0]["caption"] == "Svetec – Raw"


def test_preview_multiword_query_finds_track(cfg, voice_calls, fake_preview, tmp_path):
    _track(tmp_path, cfg, artist="Svetec", title="Raw")

    result = bot.preview_reply(cfg, "/preview svetec raw", chat_id="42")

    assert result is None
    assert len(voice_calls) == 1


def test_preview_multiword_query_partial_word_fails(cfg, text_calls, voice_calls, fake_preview, tmp_path):
    _track(tmp_path, cfg, artist="Svetec", title="Raw")

    reply = bot.preview_reply(cfg, "/preview svetec brutzelknödel")

    assert reply is not None
    assert "brutzelknödel" in reply
    assert voice_calls == []


def test_preview_multiple_matches_list_with_ids(cfg, text_calls, voice_calls, fake_preview, tmp_path):
    _track(tmp_path, cfg, artist="Svetec", title="Raw")
    _track(tmp_path, cfg, artist="Svetec", title="Brutal")
    _track(tmp_path, cfg, artist="Svetec", title="Hard")

    reply = bot.preview_reply(cfg, "/preview svetec")

    with TrackDB(cfg["state"]["track_db_path"]) as db:
        found = db.search_tracks("svetec")
    assert len(found) == 3
    for rec in found:
        assert f"{rec.id}: Svetec – " in reply
    assert "/preview <id>" in reply
    assert voice_calls == []


def test_preview_more_than_five_matches_hint(cfg, text_calls, voice_calls, fake_preview, tmp_path):
    for i in range(7):
        _track(tmp_path, cfg, artist="Svetec", title=f"Track {i:02d}")

    reply = bot.preview_reply(cfg, "/preview svetec")

    assert "weitere" in reply
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        assert len(db.search_tracks("svetec")) == 6  # limit-Default
    assert voice_calls == []


def test_preview_percent_wildcard_does_not_match_everything(cfg, text_calls, voice_calls, fake_preview, tmp_path):
    _track(tmp_path, cfg, artist="Svetec", title="Raw")

    reply = bot.preview_reply(cfg, "/preview 100%")

    assert reply is not None
    assert "100%" in reply
    assert voice_calls == []


def test_preview_missing_file_answers_clearly(cfg, text_calls, voice_calls, fake_preview, tmp_path):
    _track(tmp_path, cfg, path=tmp_path / "gibt-es-nicht.mp3", create_file=False)

    reply = bot.preview_reply(cfg, "/preview svetec")

    assert reply is not None
    assert "nicht gefunden" in reply
    assert voice_calls == []


def test_preview_extraction_failure_answers_clearly(cfg, text_calls, voice_calls, fake_preview, tmp_path):
    _track(tmp_path, cfg)
    fake_preview["fail"] = True

    reply = bot.preview_reply(cfg, "/preview svetec")

    assert reply is not None
    assert "Preview" in reply
    assert voice_calls == []


def test_preview_telegram_error_returns_message_and_throws_nothing(cfg, text_calls, voice_calls, fake_preview, monkeypatch, tmp_path):
    _track(tmp_path, cfg)

    def boom(cfg_obj, voice_path, caption=None, *, chat_id=None):
        raise bot.TelegramError("sendVoice: Telegram 400: chat not found")

    monkeypatch.setattr(output, "send_telegram_voice", boom)

    reply = bot.preview_reply(cfg, "/preview svetec")  # wirft nicht

    assert reply is not None
    assert "gesendet" in reply
    assert "123:abc" not in reply  # Token nie in der Meldung


def test_preview_temp_ogg_removed_on_success_and_failure(cfg, voice_calls, fake_preview, tmp_path):
    _track(tmp_path, cfg)

    bot.preview_reply(cfg, "/preview svetec", chat_id="42")
    # Nach dem Erfolg ist die temporäre Datei gelöscht (der Fake prüft oben,
    # dass sie beim Versand noch existierte):
    sent_path = voice_calls[0]["path"]
    assert not sent_path.exists()

    fake_preview["fail"] = True
    bot.preview_reply(cfg, "/preview svetec")
    assert fake_preview["out_path"] is not None
    assert not fake_preview["out_path"].exists()  # auch im Fehlerfall aufgeräumt


def test_handle_message_sends_preview_reply_text_once(cfg, text_calls, voice_calls, fake_preview, tmp_path):
    _track(tmp_path, cfg)

    bot.handle_message(cfg, None, "42", "/preview xyz-ohne-treffer")

    assert len(text_calls) == 1
    assert text_calls[0][0] == "sendMessage"
    assert voice_calls == []


# --- Tests für send_telegram_voice selbst (in tests/test_bot_preview.py) ---

def test_send_telegram_voice_uses_sendvoice_multipart(tmp_path, monkeypatch):
    seen = {}

    def fake(token, method, **kw):
        seen.update(method=method, kw=kw)
        return {"ok": True}

    monkeypatch.setattr(output, "telegram_call", fake)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    ogg = tmp_path / "preview.ogg"
    ogg.write_bytes(b"OggS-fake")
    cfg_obj = Config.load(ROOT / "config.yaml")

    output.send_telegram_voice(cfg_obj, ogg, caption="Svetec – Raw")

    assert seen["method"] == "sendVoice"
    assert seen["kw"]["data"]["chat_id"] == "42"
    assert seen["kw"]["data"]["caption"] == "Svetec – Raw"
    assert seen["kw"]["files"]["voice"][0].endswith(".ogg")


def test_send_telegram_voice_without_token_logs_only(tmp_path, monkeypatch):
    called = []

    def fake(token, method, **kw):
        called.append(method)

    monkeypatch.setattr(output, "telegram_call", fake)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    ogg = tmp_path / "preview.ogg"
    ogg.write_bytes(b"OggS-fake")
    cfg_obj = Config.load(ROOT / "config.yaml")

    output.send_telegram_voice(cfg_obj, ogg)  # wirft nicht

    assert called == []
