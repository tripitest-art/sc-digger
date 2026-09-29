"""Unit-Tests für Harmonic Mixing (sc_digger/harmonic.py)."""
import pytest

from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.harmonic import compatible_keys, find_mix_candidates, format_mix_list


def mk_rec(path, key, bpm, status=TrackStatus.ARCHIVE, artist="A", title="T", quality=None):
    return TrackRecord(
        path=path,
        mtime=1.0,
        size=1,
        key_camelot=key,
        bpm=bpm,
        status=status,
        artist=artist,
        title=title,
        quality_status=quality,
    )


@pytest.fixture
def empty_db(tmp_path):
    with TrackDB(tmp_path / "test.sqlite") as d:
        yield d


def test_find_mix_candidates_empty_db(empty_db):
    """Leere DB liefert leere Trefferliste."""
    got = find_mix_candidates(empty_db, "5A", 155.0)
    assert got == []


def test_find_mix_candidates_tolerance_boundary_inclusive(empty_db):
    """Toleranzgrenzen (Ziel - Tol und Ziel + Tol) werden exakt einschließlich erfasst."""
    # Ziel: 155.0, Toleranz: 3.0 -> Gültiger Bereich: [152.0, 158.0]
    empty_db.upsert_track(mk_rec("/m/exact_lower.wav", "5A", 152.0))
    empty_db.upsert_track(mk_rec("/m/exact_upper.wav", "5A", 158.0))
    empty_db.upsert_track(mk_rec("/m/too_low.wav", "5A", 151.99))
    empty_db.upsert_track(mk_rec("/m/too_high.wav", "5A", 158.01))

    candidates = find_mix_candidates(empty_db, "5A", 155.0, bpm_tolerance=3.0)
    paths = {c.path for c in candidates}
    assert paths == {"/m/exact_lower.wav", "/m/exact_upper.wav"}


def test_find_mix_candidates_limit_larger_than_matches(empty_db):
    """Wenn limit größer als die Treffermenge ist, werden alle Treffer geliefert."""
    for i in range(3):
        empty_db.upsert_track(mk_rec(f"/m/t{i}.wav", "5A", 155.0))

    got = find_mix_candidates(empty_db, "5A", 155.0, limit=50)
    assert len(got) == 3


def test_find_mix_candidates_invalid_key_raises(empty_db):
    """Ungültiger Key wirft ValueError."""
    with pytest.raises(ValueError):
        find_mix_candidates(empty_db, "INVALID", 155.0)


def test_find_mix_candidates_excludes_all_bad_qualities(empty_db):
    """Schließt fake_transcode, clipped und corrupt aus."""
    empty_db.upsert_track(mk_rec("/m/clipped.wav", "5A", 155.0, quality=QualityStatus.CLIPPED))
    empty_db.upsert_track(mk_rec("/m/corrupt.wav", "5A", 155.0, quality=QualityStatus.CORRUPT))
    empty_db.upsert_track(mk_rec("/m/fake.wav", "5A", 155.0, quality=QualityStatus.FAKE_TRANSCODE))
    empty_db.upsert_track(mk_rec("/m/ok.wav", "5A", 155.0, quality=QualityStatus.OK))

    got = find_mix_candidates(empty_db, "5A", 155.0)
    assert len(got) == 1
    assert got[0].path == "/m/ok.wav"


def test_format_mix_list_fallback_artist_and_title():
    """Fehlender Artist wird als '?' und fehlender Titel als Dateipfad formatiert."""
    records = [
        TrackRecord(
            path="/music/inbox/unknown.mp3",
            mtime=1.0,
            size=1,
            key_camelot="8B",
            bpm=150.0,
            status=TrackStatus.INBOX,
            artist=None,
            title=None,
        )
    ]
    formatted = format_mix_list("8B", 150.0, records)
    lines = formatted.split("\n")
    assert lines[0] == "🎛 8B · 150 BPM ±3: 1 Track"
    assert lines[1] == "? – /music/inbox/unknown.mp3 · 150.0 BPM · 8B"


def test_format_mix_list_plural_tracks_and_float_tolerance():
    """Ab 2 Tracks wird 'Tracks' verwendet; Dezimalstellen in BPM/Toleranz werden formatiert."""
    records = [
        TrackRecord(
            path="/m/1.wav", mtime=1.0, size=1, key_camelot="12A", bpm=152.5,
            status=TrackStatus.ARCHIVE, artist="Artist 1", title="Title 1",
        ),
        TrackRecord(
            path="/m/2.wav", mtime=1.0, size=1, key_camelot="1A", bpm=154.0,
            status=TrackStatus.ARCHIVE, artist="Artist 2", title="Title 2",
        ),
    ]
    formatted = format_mix_list("12A", 153.5, records, bpm_tolerance=1.5)
    lines = formatted.split("\n")
    assert lines[0] == "🎛 12A · 153.5 BPM ±1.5: 2 Tracks"
    assert lines[1] == "Artist 1 – Title 1 · 152.5 BPM · 12A"
    assert lines[2] == "Artist 2 – Title 2 · 154.0 BPM · 1A"


def test_compatible_keys_boundary_cases():
    """Prüft das Schließen des Rads für 1 und 12 sowohl für A als auch B."""
    assert compatible_keys("1B") == ["1B", "12B", "2B", "1A"]
    assert compatible_keys("12A") == ["12A", "11A", "1A", "12B"]
