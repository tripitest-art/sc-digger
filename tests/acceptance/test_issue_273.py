"""Akzeptanztests: Chromaprint-Such-Kern „Mehr davon“. """
from sc_digger.db import TrackRecord, TrackStatus
from sc_digger.similarity import (
    SimilarCandidate,
    SimilarResult,
    find_similar,
    format_similar_list,
)

WORDS = 200


def rec(title, bpm=140, key="5A"):
    return TrackRecord(
        path=f"/sammlung/{title}.wav",
        mtime=1.0,
        size=1,
        key_camelot=key,
        bpm=bpm,
        status=TrackStatus.ARCHIVE,
        artist="A",
        title=title,
        quality_status=None,
    )


def zero_fp():
    return [0] * WORDS


def flip_n_bits(values, n_bits, seed=42):
    """Kippt deterministisch n_bits Bits (BER = n_bits / (WORDS * 32))."""
    out = list(values)
    state = seed
    flipped = set()
    total = WORDS * 32
    while len(flipped) < n_bits:
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        pos = state % total
        if pos not in flipped:
            flipped.add(pos)
            word, bit = divmod(pos, 32)
            out[word] ^= 1 << bit
    return out


def test_find_similar_excludes_self_and_sorts_ascending():
    query = zero_fp()
    near = flip_n_bits(query, int(0.20 * WORDS * 32))   # BER ≈ 0.20 → behalten
    far = [0xFFFFFFFF] * WORDS                           # BER = 1.0 → behalten
    candidates = [
        SimilarCandidate(rec("self"), query),            # BER 0 → ausschließen
        SimilarCandidate(rec("far"), far),
        SimilarCandidate(rec("near"), near),
    ]
    results = find_similar(query, candidates)
    assert [r.track.title for r in results] == ["near", "far"]
    assert results[0].ber < results[1].ber


def test_find_similar_top_n_limit():
    query = zero_fp()
    candidates = [
        SimilarCandidate(rec(f"t{i}"), flip_n_bits(query, int(0.2 * WORDS * 32), seed=i))
        for i in range(10)
    ]
    assert len(find_similar(query, candidates, top_n=5)) == 5


def test_find_similar_empty_when_all_excluded_or_no_candidates():
    query = zero_fp()
    assert find_similar(query, [SimilarCandidate(rec("dup"), query)]) == []
    assert find_similar(query, []) == []


def test_format_similar_list_orders_and_scores():
    results = [
        SimilarResult(rec("Near"), ber=0.2),
        SimilarResult(rec("Far"), ber=1.0),
    ]
    text = format_similar_list(results)
    assert "Ähnliche Aufnahmen" in text
    assert text.index("Near") < text.index("Far")
    assert "Ähnlichkeit 80.0%" in text
    assert "Ähnlichkeit 0.0%" in text


def test_format_similar_list_empty():
    assert format_similar_list([]) == "Keine ähnlichen Aufnahmen gefunden."
