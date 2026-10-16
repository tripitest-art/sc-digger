"""Eigene Tests für den Chromaprint-Such-Kern „Mehr davon“ (Issue #273).

Reine Vergleichs- und Formatlogik ohne Netzwerk: die Kandidaten werden im Test direkt
aufgebaut (Fingerprint-Synthese wie in tests/test_bot_similar.py). Die Tests für
`build_similar_reply` (Issue #274) nutzen dazu eine temporäre SQLite-Track-DB.
"""
import sqlite3

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.fingerprint import encode_fingerprint
from sc_digger.similarity import (
    NO_SIMILAR,
    SIMILAR_FAILED,
    SimilarCandidate,
    SimilarResult,
    build_similar_reply,
    find_similar,
    format_similar_list,
)

WORDS = 200


def _rec(title, *, artist="A", bpm=140, key="5A"):
    return TrackRecord(
        path=f"/sammlung/{title}.wav",
        mtime=1.0,
        size=1,
        artist=artist,
        title=title,
        bpm=bpm,
        key_camelot=key,
    )


def _every_fifth_bit(values):
    """Kopie mit jedem fünften Bit gekippt: BER exakt 0.20 (20 % der Bits)."""
    out = list(values)
    for pos in range(0, len(values) * 32, 5):
        word, bit = divmod(pos, 32)
        out[word] ^= 1 << bit
    return out


def test_find_similar_top_n_zero_returns_empty():
    """top_n=0 heißt: nichts ausgeben, aber auch kein Fehler."""
    query = [0] * WORDS
    candidates = [SimilarCandidate(_rec("near"), _every_fifth_bit(query))]
    assert find_similar(query, candidates, top_n=0) == []


def test_find_similar_no_candidates_returns_empty():
    query = [0] * WORDS
    assert find_similar(query, []) == []


def test_find_similar_keeps_input_order_when_ber_is_equal():
    """Gleiche BER = Gleichstand: die Reihenfolge der Kandidaten bleibt erhalten."""
    query = [0] * WORDS
    fingerprint = _every_fifth_bit(query)
    candidates = [
        SimilarCandidate(_rec("zuerst"), fingerprint),
        SimilarCandidate(_rec("danach"), fingerprint),
    ]
    results = find_similar(query, candidates, top_n=2)
    assert [r.track.title for r in results] == ["zuerst", "danach"]
    assert results[0].ber == results[1].ber


def test_find_similar_accepts_iterables():
    """Kandidaten dürfen auch ein Generator sein (kein Listen-Zwang)."""
    query = [0] * WORDS
    candidates = (SimilarCandidate(_rec(f"t{i}"), _every_fifth_bit(query)) for i in range(3))
    assert len(find_similar(query, candidates, top_n=2)) == 2


def test_format_similar_list_ranks_from_one_and_mixes_missing_values():
    """Rang beginnt bei 1; fehlende Werte werden als '–' ausgegeben."""
    results = [
        SimilarResult(_rec("Alpha", bpm=140.0, key="5A"), ber=0.2),
        SimilarResult(_rec("Beta", bpm=None, key=None), ber=0.5),
    ]
    lines = format_similar_list(results).splitlines()
    assert lines[0] == "🎧 Ähnliche Aufnahmen:"
    assert lines[1] == "1. A – Alpha · 140.0 BPM · 5A · Ähnlichkeit 80.0%"
    assert lines[2] == "2. A – Beta · – BPM · – · Ähnlichkeit 50.0%"


def test_format_similar_list_missing_artist_and_title():
    """Ohne Artist/Titel steht '–' statt 'None' in der Zeile."""
    results = [SimilarResult(_rec("x", artist=None, bpm=None), ber=0.2)]
    results[0].track.title = None
    assert format_similar_list(results).splitlines()[1] == "1. – – – · – BPM · 5A · Ähnlichkeit 80.0%"


def test_format_similar_list_empty_is_standard_text():
    assert format_similar_list([]) == NO_SIMILAR
    assert NO_SIMILAR == "Keine ähnlichen Aufnahmen gefunden."


def _insert(db, title, values, *, bpm=140, key="5A"):
    """Legt einen Track mit Fingerprint in der temporären DB ab."""
    db.upsert_track(
        TrackRecord(
            path=f"/inbox/{title}.wav",
            mtime=1.0,
            size=1,
            artist="A",
            title=title,
            bpm=bpm,
            key_camelot=key,
            fingerprint=encode_fingerprint(values),
            fingerprint_duration=60.0,
        )
    )


def test_fingerprinted_tracks_returns_values_and_skips_tracks_without_fingerprint(tmp_path):
    """Nur Tracks mit Fingerprint kommen zurück – als Track plus Rohwerte."""
    values = [0] * WORDS
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        _insert(db, "mit", values)
        db.upsert_track(TrackRecord(path="/inbox/ohne.wav", mtime=1.0, size=1, title="ohne"))

        rows = db.fingerprinted_tracks()

    assert [rec.title for rec, _ in rows] == ["mit"]
    assert rows[0][1] == values


def test_fingerprinted_tracks_empty_db_returns_empty_list(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        assert db.fingerprinted_tracks() == []


def test_build_similar_reply_uses_db_fingerprints(tmp_path):
    """Der Bot-Text kommt aus der DB: der Query selbst (BER 0) fällt raus, der nahe Track nicht."""
    query = [0] * WORDS
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        _insert(db, "self", query)
        _insert(db, "near", _every_fifth_bit(query))
        reply = build_similar_reply(db, query, top_n=5)

    assert reply.splitlines()[0] == "🎧 Ähnliche Aufnahmen:"
    assert reply.splitlines()[1] == "1. A – near · 140.0 BPM · 5A · Ähnlichkeit 80.0%"
    assert "self" not in reply


def test_build_similar_reply_empty_db_is_no_similar(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        assert build_similar_reply(db, [0] * WORDS, top_n=5) == NO_SIMILAR


def test_build_similar_reply_logs_and_reports_db_error():
    """Kann die DB nicht gelesen werden, kommt der Standardtext statt einer Ausnahme."""

    class _BrokenDB:
        def fingerprinted_tracks(self):
            raise sqlite3.OperationalError("database is locked")

    assert build_similar_reply(_BrokenDB(), [0] * WORDS, top_n=5) == SIMILAR_FAILED
