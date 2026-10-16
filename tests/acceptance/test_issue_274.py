"""Akzeptanztests: „Mehr davon“-Antwort aus der Track-DB. """
import pytest

from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.fingerprint import encode_fingerprint
from sc_digger.similarity import build_similar_reply

WORDS = 200


@pytest.fixture
def db(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as d:
        yield d


def rec(title):
    return TrackRecord(
        path=f"/sammlung/{title}.wav",
        mtime=1.0,
        size=1,
        key_camelot="5A",
        bpm=140,
        status=TrackStatus.ARCHIVE,
        artist="A",
        title=title,
        quality_status=None,
    )


def test_build_similar_reply_excludes_self_and_returns_variant_first(db):
    self_rec = db.upsert_track(rec("self"))
    near_rec = db.upsert_track(rec("near"))
    far_rec = db.upsert_track(rec("far"))

    query = [0] * WORDS
    near = [0] * WORDS
    # Deterministisch 20 % Bits kippen (BER ≈ 0.20 -> 80 % Ähnlichkeit)
    for pos in range(0, WORDS * 32, 5):
        word, bit = divmod(pos, 32)
        near[word] ^= 1 << bit

    db.set_fingerprint(self_rec.id, encode_fingerprint([0] * WORDS), 60.0)
    db.set_fingerprint(near_rec.id, encode_fingerprint(near), 60.0)
    db.set_fingerprint(far_rec.id, encode_fingerprint([0xFFFFFFFF] * WORDS), 60.0)

    reply = build_similar_reply(db, query)
    assert "self" not in reply
    assert reply.index("near") < reply.index("far")
    assert "Ähnlichkeit 80.0%" in reply


def test_build_similar_reply_empty_db(db):
    assert build_similar_reply(db, [0] * WORDS) == "Keine ähnlichen Aufnahmen gefunden."
