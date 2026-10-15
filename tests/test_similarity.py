"""Eigene Tests für den Chromaprint-Such-Kern „Mehr davon“ (Issue #273).

Reine Vergleichs- und Formatlogik, kein Netzwerk und keine DB nötig: die Kandidaten
werden im Test direkt aufgebaut (Fingerprint-Synthese wie in tests/test_bot_similar.py).
"""
from sc_digger.db import TrackRecord
from sc_digger.similarity import (
    NO_SIMILAR,
    SimilarCandidate,
    SimilarResult,
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
