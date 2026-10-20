"""Akzeptanztests: Embedding-Ähnlichkeit im Telegram-Bot mit Chromaprint-Fallback."""
from pathlib import Path
import pytest

from sc_digger import bot
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.fingerprint import encode_fingerprint


@pytest.fixture
def db(tmp_path: Path):
    with TrackDB(tmp_path / "tracks.sqlite") as d:
        yield d


def _make_track(path: str, **kw) -> TrackRecord:
    return TrackRecord(path=path, mtime=1.0, size=1000, **kw)


def test_get_track_by_source_url(db: TrackDB):
    t = db.upsert_track(_make_track("/m/1.mp3", source_url="https://soundcloud.com/artist/hit"))
    found = db.get_track_by_source_url("https://soundcloud.com/artist/hit")
    assert found is not None
    assert found.id == t.id
    assert db.get_track_by_source_url("https://soundcloud.com/artist/other") is None
    assert db.get_track_by_source_url("") is None


def test_similar_reply_from_track_id_with_embedding(db: TrackDB):
    t1 = db.upsert_track(_make_track("/m/1.mp3", artist="Artist 1", title="Track 1"))
    t2 = db.upsert_track(_make_track("/m/2.mp3", artist="Artist 2", title="Track 2", bpm=150.0, key_camelot="8A"))
    db.set_embedding(t1.id, "effnet", [1.0, 0.0])
    db.set_embedding(t2.id, "effnet", [0.95, 0.05])

    reply = bot.similar_reply_from_track_id(db, t1.id)
    assert "🎧 Klanglich ähnlich:" in reply
    assert "Artist 2 – Track 2" in reply
    assert "Track 1" not in reply


def test_similar_reply_from_track_id_not_found(db: TrackDB):
    reply = bot.similar_reply_from_track_id(db, 99999)
    assert reply == bot.SIMILAR_TRACK_NOT_FOUND


def test_similar_reply_from_track_id_fallback_chromaprint(db: TrackDB):
    fp1 = encode_fingerprint([1000] * 50)
    fp2 = encode_fingerprint([0] * 50)
    t1 = db.upsert_track(_make_track("/m/1.mp3", artist="A1", title="T1", fingerprint=fp1))
    t2 = db.upsert_track(_make_track("/m/2.mp3", artist="A2", title="T2", fingerprint=fp2, bpm=152.0, key_camelot="9A"))

    reply = bot.similar_reply_from_track_id(db, t1.id)
    assert "🎧 Klanglich ähnlich:" not in reply
    assert "A2 – T2" in reply


def test_similar_reply_from_track_id_no_features(db: TrackDB):
    t = db.upsert_track(_make_track("/m/bare.mp3"))
    reply = bot.similar_reply_from_track_id(db, t.id)
    assert reply == bot.SIMILAR_NO_FEATURES


def test_similar_reply_from_url_uses_embedding_without_audio_fetch(db: TrackDB):
    url = "https://soundcloud.com/artist/hit"
    t1 = db.upsert_track(_make_track("/m/1.mp3", artist="Artist 1", title="Hit Track", source_url=url))
    t2 = db.upsert_track(_make_track("/m/2.mp3", artist="Artist 2", title="Similar Sound", bpm=150.0, key_camelot="8A"))
    db.set_embedding(t1.id, "effnet", [1.0, 0.0])
    db.set_embedding(t2.id, "effnet", [0.95, 0.05])

    def boom(u, dest):
        raise AssertionError("Audio-Download darf bei vorhandenem Embedding nicht aufgerufen werden")

    reply = bot.similar_reply_from_url(None, db, url, fetch_audio=boom)
    assert "🎧 Klanglich ähnlich:" in reply
    assert "Artist 2 – Similar Sound" in reply


def test_similar_reply_from_url_falls_back_when_no_embedding(db: TrackDB):
    url = "https://soundcloud.com/artist/unknown"
    called = []

    def fake_fetch(u, dest):
        called.append(u)
        dest.write_bytes(b"dummy")
        return dest

    def fake_fp(path):
        return [1000] * 50

    reply = bot.similar_reply_from_url(None, db, url, fetch_audio=fake_fetch, compute_fp=fake_fp)
    assert called == [url]
    assert "🎧 Klanglich ähnlich:" not in reply
