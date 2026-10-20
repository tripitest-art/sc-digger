"""Eigene Tests für „Mehr davon“ im Telegram-Bot per SoundCloud-URL (Issue #272).

Kein echter Netzwerkzugriff: Audio-Beschaffung und Fingerprint sind durch Fakes ersetzt
(bzw. die Standard-Helfer per monkeypatch), die Track-DB ist eine temporäre SQLite-Datei.
"""
from pathlib import Path

import pytest

from sc_digger import bot, output, similarity
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.fingerprint import Fingerprint, encode_fingerprint
from sc_digger.models import Config

URL = "https://soundcloud.com/a/t1"
WORDS = 200


def _near(values: list[int]) -> list[int]:
    """Kopie mit jedem fünften Bit gekippt: BER exakt 0.20 (20 % der Bits)."""
    out = list(values)
    for pos in range(0, len(values) * 32, 5):
        word, bit = divmod(pos, 32)
        out[word] ^= 1 << bit
    return out


def _fingerprinted(db: TrackDB, tmp_path: Path, artist: str, title: str, values: list[int]) -> TrackRecord:
    p = tmp_path / f"{artist} - {title}.wav"
    p.write_bytes(b"fake-audio")
    rec = db.upsert_track(
        TrackRecord(
            path=str(p), mtime=1.0, size=10, artist=artist, title=title,
            bpm=140, key_camelot="5A",
        )
    )
    db.set_fingerprint(rec.id, encode_fingerprint(values), 60.0)
    return rec


def _fake_fetch(url: str, dest: Path) -> Path:
    dest.write_bytes(b"fake-audio")
    return dest


def test_similar_reply_from_url_lists_similar_track(tmp_path):
    """Der Link-Weg liefert dieselbe Antwort wie eine gesendete Datei: Top-Treffer mit Ähnlichkeit."""
    with TrackDB(tmp_path / "t.sqlite") as db:
        _fingerprinted(db, tmp_path, "Svetec", "Raw", _near([0] * WORDS))
        reply = bot.similar_reply_from_url(
            None, db, URL, fetch_audio=_fake_fetch, compute_fp=lambda path: [0] * WORDS
        )
    assert reply.splitlines()[0] == "🎧 Ähnliche Aufnahmen:"
    assert reply.splitlines()[1] == "1. Svetec – Raw · 140.0 BPM · 5A · Ähnlichkeit 80.0%"


def test_similar_reply_from_url_missing_fingerprint_returns_standard_text(tmp_path):
    """Ohne Fingerprint (fpcalc-Fehler) gibt es den Standard-Fehlertext statt einer Ausnahme."""
    with TrackDB(tmp_path / "t.sqlite") as db:
        reply = bot.similar_reply_from_url(
            None, db, URL, fetch_audio=_fake_fetch, compute_fp=lambda path: None
        )
    assert reply == similarity.SIMILAR_FAILED


def test_similar_reply_from_url_removes_temp_file_on_success(tmp_path):
    seen = {}

    def fetch(url, dest):
        dest.write_bytes(b"fake-audio")
        seen["path"] = dest
        return dest

    with TrackDB(tmp_path / "t.sqlite") as db:
        bot.similar_reply_from_url(
            None, db, URL, fetch_audio=fetch, compute_fp=lambda path: [0] * WORDS
        )
    assert not seen["path"].exists()


def test_similar_reply_from_url_removes_temp_file_on_error(tmp_path):
    """Auch wenn der Download scheitert, bleibt keine temporäre Datei liegen."""
    seen = {}

    def boom(url, dest):
        seen["path"] = dest
        raise RuntimeError("kaputt")

    with TrackDB(tmp_path / "t.sqlite") as db:
        reply = bot.similar_reply_from_url(None, db, URL, fetch_audio=boom)
    assert reply == similarity.SIMILAR_FAILED
    assert not seen["path"].exists()


@pytest.fixture
def cfg(tmp_path):
    return Config({"state": {"track_db_path": str(tmp_path / "tracks.sqlite")}})


@pytest.fixture
def sent(monkeypatch):
    """Fängt die Bot-Antworten ab; Netzwerk- und Fingerprint-Helfer sind gefakt."""
    out = []
    monkeypatch.setattr(bot, "_send_text", lambda c, chat_id, msg: out.append(msg))
    monkeypatch.setattr(bot, "_fetch_audio_via_soundcloud", _fake_fetch)
    monkeypatch.setattr(bot, "_compute_fingerprint_values", lambda path: [0] * WORDS)
    return out


def test_handle_message_routes_similar_url(cfg, sent):
    """'/similar <Link>' läuft über die Ähnlichkeitssuche, nicht über den Playlist-Check."""
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        _fingerprinted(db, Path(cfg["state"]["track_db_path"]).parent, "Svetec", "Raw", _near([0] * WORDS))

    bot.handle_message(cfg, None, "42", f"/similar {URL}")

    assert len(sent) == 1
    assert "1. Svetec – Raw" in sent[0]


def test_handle_message_similar_url_without_hit(cfg, sent):
    bot.handle_message(cfg, None, "42", f"/similar {URL}")

    assert sent == ["Keine ähnlichen Aufnahmen gefunden."]


def test_handle_message_similar_without_url_sends_usage(cfg, sent):
    bot.handle_message(cfg, None, "42", "/similar")

    assert sent == [bot.SIMILAR_USAGE]


def test_similar_is_in_help_text():
    assert "/similar" in bot.HELP_TEXT


def test_similar_reply_from_audio_fingerprints_sent_file(tmp_path, monkeypatch):
    """Eine gesendete Datei liefert dieselbe Antwort wie der Link-Weg."""
    seen = {}

    def fake_compute(path, **kwargs):
        seen["path"] = Path(path)
        return Fingerprint(values=[0] * WORDS, duration=60.0)

    monkeypatch.setattr(bot, "compute_fingerprint", fake_compute)
    audio = tmp_path / "gesendet.oga"
    audio.write_bytes(b"fake-audio")

    with TrackDB(tmp_path / "t.sqlite") as db:
        _fingerprinted(db, tmp_path, "Svetec", "Raw", _near([0] * WORDS))
        reply = bot.similar_reply_from_audio(None, db, audio)

    assert seen["path"] == audio
    assert reply.splitlines()[0] == "🎧 Ähnliche Aufnahmen:"
    assert reply.splitlines()[1] == "1. Svetec – Raw · 140.0 BPM · 5A · Ähnlichkeit 80.0%"


def test_similar_reply_from_audio_without_fingerprint_returns_standard_text(tmp_path, monkeypatch):
    """Ohne Fingerprint (fpcalc-Fehler) gibt es den Standard-Fehlertext statt einer Ausnahme."""
    monkeypatch.setattr(bot, "compute_fingerprint", lambda path, **kwargs: None)

    with TrackDB(tmp_path / "t.sqlite") as db:
        reply = bot.similar_reply_from_audio(None, db, tmp_path / "faul.oga")

    assert reply == similarity.SIMILAR_FAILED


def test_handle_audio_message_downloads_and_removes_temp_file(cfg, sent, monkeypatch):
    """Eine Audionachricht wird geladen, ausgewertet und als Top-Liste beantwortet."""
    seen = {}

    def fake_download(c, file_id, dest):
        seen["file_id"] = file_id
        seen["path"] = Path(dest)
        Path(dest).write_bytes(b"fake-audio")
        return dest

    monkeypatch.setattr(bot, "_download_telegram_audio", fake_download)
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        _fingerprinted(db, Path(cfg["state"]["track_db_path"]).parent, "Svetec", "Raw", _near([0] * WORDS))

    bot.handle_audio_message(cfg, "42", {"file_id": "F1"})

    assert seen["file_id"] == "F1"
    assert "1. Svetec – Raw" in sent[0]
    assert not seen["path"].exists()


def test_handle_audio_message_download_error_reports_standard_text(cfg, sent, monkeypatch):
    def boom(c, file_id, dest):
        raise bot.TelegramError("kaputt")

    monkeypatch.setattr(bot, "_download_telegram_audio", boom)

    bot.handle_audio_message(cfg, "42", {"file_id": "F1"})

    assert sent == [similarity.SIMILAR_FAILED]


def test_handle_audio_message_without_file_id_does_nothing(cfg, sent):
    bot.handle_audio_message(cfg, "42", {"duration": 3})

    assert sent == []


def _file_get(monkeypatch, *, status_code=200, content=b"audio-bytes", file_path="voice/file_1.oga"):
    """Fakt `telegram_call` (getFile) und den Datei-Download; gibt die Aufrufe zurück."""
    calls = {}

    def fake_call(token, method, **kwargs):
        calls["method"] = method
        calls["params"] = kwargs.get("params")
        return {"ok": True, "result": {"file_path": file_path}}

    class _Resp:
        pass

    resp = _Resp()
    resp.status_code = status_code
    resp.content = content

    def fake_get(url, timeout=None):
        calls["url"] = url
        return resp

    monkeypatch.setattr(bot, "telegram_call", fake_call)
    monkeypatch.setattr(output.requests, "get", fake_get)
    return calls


def test_download_telegram_audio_gets_file_and_writes_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:AABBCCDDEEFFGGHHIIJJ")
    calls = _file_get(monkeypatch)

    dest = tmp_path / "empfangen.oga"
    out = bot._download_telegram_audio(Config({}), "F1", dest)

    assert out == dest
    assert dest.read_bytes() == b"audio-bytes"
    assert calls["method"] == "getFile"
    assert calls["params"] == {"file_id": "F1"}
    assert calls["url"].startswith("https://api.telegram.org/file/bot")
    assert calls["url"].endswith("/voice/file_1.oga")


def test_download_telegram_audio_reports_http_error_without_token(tmp_path, monkeypatch):
    token = "123456:AABBCCDDEEFFGGHHIIJJ"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    _file_get(monkeypatch, status_code=404, content=b"nope")

    with pytest.raises(bot.TelegramError) as err:
        bot._download_telegram_audio(Config({}), "F1", tmp_path / "empfangen.oga")

    assert token not in str(err.value)


def test_download_telegram_audio_redacts_token_from_request_errors(tmp_path, monkeypatch):
    """requests schreibt die URL in die Fehlermeldung – der Bot-Token darf nicht durchkommen."""
    token = "123456:AABBCCDDEEFFGGHHIIJJ"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    _file_get(monkeypatch)

    def boom(url, timeout=None):
        raise output.requests.RequestException(f"HTTPError für {url}")

    monkeypatch.setattr(output.requests, "get", boom)

    with pytest.raises(bot.TelegramError) as err:
        bot._download_telegram_audio(Config({}), "F1", tmp_path / "empfangen.oga")

    assert token not in str(err.value)
    assert "bot***" in str(err.value)


def test_listen_routes_voice_and_audio_to_audio_handler(monkeypatch):
    """Polling-Routing: voice/audio geht an handle_audio_message, nicht an handle_message."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "4242")
    monkeypatch.setattr(bot, "SoundCloudClient", lambda: None)
    seen = []

    updates = iter([
        {"result": []},                                                      # Backlog beim Start
        {"result": [
            {"update_id": 7, "message": {"chat": {"id": 4242}, "voice": {"file_id": "V1"}}},
            {"update_id": 8, "message": {"chat": {"id": 4242}, "audio": {"file_id": "A1"}}},
        ]},
    ])

    def fake_call(token, method, **kwargs):
        try:
            return next(updates)
        except StopIteration:
            raise KeyboardInterrupt                                      # Loop beenden

    monkeypatch.setattr(bot, "telegram_call", fake_call)
    monkeypatch.setattr(bot, "handle_message", lambda *a: pytest.fail("Text-Handler gerufen"))
    monkeypatch.setattr(bot, "handle_audio_message",
                        lambda cfg, chat_id, audio: seen.append((chat_id, audio)))

    with pytest.raises(KeyboardInterrupt):
        bot.listen(Config({}))

    assert seen == [("4242", {"file_id": "V1"}), ("4242", {"file_id": "A1"})]


def test_handle_message_routes_similar_id_with_embedding(cfg, sent):
    """'/similar <id>' liefert klangliche Embeddings, falls vorhanden."""
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        t1 = db.upsert_track(TrackRecord(path="/m/1.mp3", mtime=1.0, size=10, artist="A1", title="T1"))
        t2 = db.upsert_track(TrackRecord(path="/m/2.mp3", mtime=1.0, size=10, artist="A2", title="T2", bpm=150, key_camelot="8A"))
        db.set_embedding(t1.id, "effnet", [1.0, 0.0])
        db.set_embedding(t2.id, "effnet", [0.99, 0.01])

    bot.handle_message(cfg, None, "42", f"/similar {t1.id}")

    assert len(sent) == 1
    assert "🎧 Klanglich ähnlich:" in sent[0]
    assert "A2 – T2" in sent[0]


def test_handle_message_routes_similar_id_fallback_chromaprint(cfg, sent):
    """'/similar <id>' fällt transparent auf Chromaprint zurück, falls kein Embedding existiert."""
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        t1 = _fingerprinted(db, Path(cfg["state"]["track_db_path"]).parent, "Artist1", "Track1", [0] * WORDS)
        _fingerprinted(db, Path(cfg["state"]["track_db_path"]).parent, "Artist2", "Track2", _near([0] * WORDS))

    bot.handle_message(cfg, None, "42", f"/similar {t1.id}")

    assert len(sent) == 1
    assert "🎧 Ähnliche Aufnahmen:" in sent[0]
    assert "Artist2 – Track2" in sent[0]


def test_handle_message_routes_similar_id_not_found(cfg, sent):
    bot.handle_message(cfg, None, "42", "/similar 99999")

    assert sent == [bot.SIMILAR_TRACK_NOT_FOUND]


def test_handle_message_routes_similar_id_no_features(cfg, sent):
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        t = db.upsert_track(TrackRecord(path="/m/bare.mp3", mtime=1.0, size=10))

    bot.handle_message(cfg, None, "42", f"/similar {t.id}")

    assert sent == [bot.SIMILAR_NO_FEATURES]


def test_handle_message_similar_invalid_arg_sends_usage(cfg, sent):
    bot.handle_message(cfg, None, "42", "/similar invalid_arg")

    assert sent == [bot.SIMILAR_USAGE]


def test_handle_message_similar_db_exception_sends_failed(cfg, sent, monkeypatch):
    class BrokenDB:
        def __enter__(self):
            raise RuntimeError("DB error")
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(bot, "TrackDB", lambda path: BrokenDB())
    bot.handle_message(cfg, None, "42", "/similar 1")

    assert sent == [similarity.SIMILAR_FAILED]


def test_similar_reply_from_audio_prefers_embedding(tmp_path, monkeypatch):
    """Audionachricht: Entspricht der Fingerprint einem DB-Track mit Embedding, wird dieses bevorzugt."""
    raw_vals = [1000] * WORDS
    monkeypatch.setattr(bot, "_compute_fingerprint_values", lambda path: raw_vals)

    with TrackDB(tmp_path / "t.sqlite") as db:
        t1 = db.upsert_track(TrackRecord(path="/m/1.mp3", mtime=1.0, size=10, artist="Hit", title="Song", fingerprint=encode_fingerprint(raw_vals)))
        t2 = db.upsert_track(TrackRecord(path="/m/2.mp3", mtime=1.0, size=10, artist="Similar", title="Vibe", bpm=150, key_camelot="8A"))
        db.set_embedding(t1.id, "effnet", [1.0, 0.0])
        db.set_embedding(t2.id, "effnet", [0.95, 0.05])

        reply = bot.similar_reply_from_audio(None, db, tmp_path / "test.oga")

    assert "🎧 Klanglich ähnlich:" in reply
    assert "Similar – Vibe" in reply


def test_similar_reply_from_audio_fallback_chromaprint(tmp_path, monkeypatch):
    """Audionachricht: Entspricht der Fingerprint einem DB-Track ohne Embedding, greift Chromaprint."""
    raw_vals = [1000] * WORDS
    monkeypatch.setattr(bot, "_compute_fingerprint_values", lambda path: raw_vals)

    with TrackDB(tmp_path / "t.sqlite") as db:
        db.upsert_track(TrackRecord(path="/m/1.mp3", mtime=1.0, size=10, artist="Hit", title="Song", fingerprint=encode_fingerprint(raw_vals)))
        _fingerprinted(db, tmp_path, "Other", "Tune", _near(raw_vals))

        reply = bot.similar_reply_from_audio(None, db, tmp_path / "test.oga")

    assert "🎧 Ähnliche Aufnahmen:" in reply
    assert "Other – Tune" in reply


def test_similar_reply_from_track_id_db_error_returns_failed(tmp_path, monkeypatch):
    with TrackDB(tmp_path / "t.sqlite") as db:
        monkeypatch.setattr(db, "get_track_by_id", lambda tid: (_ for _ in ()).throw(RuntimeError("disk error")))
        reply = bot.similar_reply_from_track_id(db, 1)

    assert reply == similarity.SIMILAR_FAILED


def test_similar_reply_from_track_id_top_n(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        t_query = db.upsert_track(TrackRecord(path="/m/q.mp3", mtime=1.0, size=10, artist="Q", title="T"))
        db.set_embedding(t_query.id, "effnet", [1.0, 0.0])
        for i in range(1, 6):
            t = db.upsert_track(TrackRecord(path=f"/m/{i}.mp3", mtime=1.0, size=10, artist=f"A{i}", title=f"T{i}"))
            db.set_embedding(t.id, "effnet", [1.0 - i * 0.01, 0.0])

        reply = bot.similar_reply_from_track_id(db, t_query.id, top_n=2)

    lines = [l for l in reply.splitlines() if l.startswith("1.") or l.startswith("2.") or l.startswith("3.")]
    assert len(lines) == 2


def test_similar_reply_from_url_db_error_falls_back_to_fetch(tmp_path, monkeypatch):
    url = "https://soundcloud.com/artist/boom"
    called = []

    def fake_fetch(u, dest):
        called.append(u)
        dest.write_bytes(b"dummy")
        return dest

    with TrackDB(tmp_path / "t.sqlite") as db:
        monkeypatch.setattr(db, "get_track_by_source_url", lambda u: (_ for _ in ()).throw(RuntimeError("db fail")))
        reply = bot.similar_reply_from_url(None, db, url, fetch_audio=fake_fetch, compute_fp=lambda p: [0] * WORDS)

    assert called == [url]
    assert reply == similarity.NO_SIMILAR

