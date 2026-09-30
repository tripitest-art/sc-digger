import pytest

from sc_digger.db import TrackRecord
from sc_digger.harmonic import format_mix_list


def _rec(key_camelot: str | None) -> TrackRecord:
    return TrackRecord(path="/inbox/a.mp3", mtime=0.0, size=1, artist="A", title="T",
                       bpm=150.0, key_camelot=key_camelot)


@pytest.mark.parametrize(
    "target,rec_key,expected",
    [
        ("5A", "6A", "A – T · 150.0 BPM · 6A (d1)"),
        ("5A", "4A", "A – T · 150.0 BPM · 4A (d1)"),
        ("5A", "5B", "A – T · 150.0 BPM · 5B (d1)"),
        ("5A", "7B", "A – T · 150.0 BPM · 7B (d3)"),
        ("12A", "1A", "A – T · 150.0 BPM · 1A (d1)"),
        ("5a", "11A", "A – T · 150.0 BPM · 11A (d6)"),
    ],
)
def test_abstand_hinter_key(target, rec_key, expected):
    lines = format_mix_list(target, 150.0, [_rec(rec_key)]).splitlines()
    assert lines[1] == expected


def test_gleicher_key_ohne_abstand():
    lines = format_mix_list("5A", 150.0, [_rec("5A")]).splitlines()
    assert lines[1] == "A – T · 150.0 BPM · 5A"


def test_ohne_key_kein_abstand():
    lines = format_mix_list("5A", 150.0, [_rec(None)]).splitlines()
    assert lines[1] == "A – T · 150.0 BPM · ?"


def test_ungueltiger_ziel_key_kein_abstand():
    lines = format_mix_list("X", 150.0, [_rec("5A")]).splitlines()
    assert lines[1] == "A – T · 150.0 BPM · 5A"