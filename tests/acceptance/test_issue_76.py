"""Akzeptanztests: Harmonic-Mixing-Kern (kompatible Camelot-Keys, Kandidaten aus der Track-DB)."""
import pytest

from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.harmonic import compatible_keys, find_mix_candidates, format_mix_list


def rec(path, key, bpm, status=TrackStatus.ARCHIVE, artist="A", title=None, quality=None):
    return TrackRecord(path=path, mtime=1.0, size=1, key_camelot=key, bpm=bpm, status=status,
                       artist=artist, title=title or path, quality_status=quality)


@pytest.fixture
def db(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as d:
        yield d


@pytest.mark.parametrize("key, expected", [
    ("5A", ["5A", "4A", "6A", "5B"]),
    ("1A", ["1A", "12A", "2A", "1B"]),     # Rad schließt sich
    ("12B", ["12B", "11B", "1B", "12A"]),
    (" 8b ", ["8B", "7B", "9B", "8A"]),    # Leerzeichen, Kleinbuchstabe
])
def test_compatible_keys(key, expected):
    assert compatible_keys(key) == expected


@pytest.mark.parametrize("bad", ["", "13A", "0B", "5C", "A5", "?", "Am"])
def test_compatible_keys_rejects_invalid(bad):
    with pytest.raises(ValueError):
        compatible_keys(bad)


def test_candidates_filter_by_key_and_bpm(db):
    db.upsert_track(rec("/m/a.wav", "5A", 155.0))
    db.upsert_track(rec("/m/b.wav", "6A", 157.9))
    db.upsert_track(rec("/m/c.wav", "5B", 152.0))
    db.upsert_track(rec("/m/d.wav", "7A", 155.0))                          # Key passt nicht
    db.upsert_track(rec("/m/e.wav", "5A", 160.5))                          # BPM zu weit weg
    db.upsert_track(rec("/m/f.wav", "5A", None))                           # ohne BPM
    db.upsert_track(rec("/m/g.wav", None, 155.0))                          # ohne Key
    db.upsert_track(rec("/m/h.wav", "4A", 155.0, status=TrackStatus.REJECTED))
    db.upsert_track(rec("/m/i.wav", "4A", 155.0, quality=QualityStatus.FAKE_TRANSCODE))
    got = [r.path for r in find_mix_candidates(db, "5A", 155)]
    assert got == ["/m/a.wav", "/m/b.wav", "/m/c.wav"]


def test_candidates_sorted_by_key_then_bpm_distance(db):
    db.upsert_track(rec("/m/1.wav", "5B", 155.0))
    db.upsert_track(rec("/m/2.wav", "5A", 157.0))
    db.upsert_track(rec("/m/3.wav", "4A", 155.0))
    db.upsert_track(rec("/m/4.wav", "5A", 155.5))
    db.upsert_track(rec("/m/5.wav", "6A", 154.0, status=TrackStatus.INBOX))
    got = [r.path for r in find_mix_candidates(db, "5A", 155)]
    assert got == ["/m/4.wav", "/m/2.wav", "/m/3.wav", "/m/5.wav", "/m/1.wav"]


def test_candidates_tolerance_and_limit(db):
    for i in range(10):
        db.upsert_track(rec(f"/m/{i}.wav", "5A", 150.0 + i))
    got = find_mix_candidates(db, "5a", 155, bpm_tolerance=1.0, limit=2)
    assert [r.bpm for r in got] == [155.0, 154.0]


def test_format_mix_list(db):
    db.upsert_track(rec("/m/a.wav", "5A", 155.04, artist="Artist One", title="Tune"))
    text = format_mix_list("5A", 155, find_mix_candidates(db, "5A", 155))
    lines = text.split("\n")
    assert lines[0] == "🎛 5A · 155 BPM ±3: 1 Track"
    assert lines[1] == "Artist One – Tune · 155.0 BPM · 5A"


def test_format_mix_list_empty():
    assert format_mix_list("5A", 155, []) == "🎛 5A · 155 BPM ±3: keine passenden Tracks"
