"""Akzeptanztests: Dauer als zweites Kriterium im Sammlungs-Abgleich."""
import subprocess
from pathlib import Path

import pytest

from sc_digger.collection import DURATION_TOLERANCE_S, Collection, probe_duration
from tests.test_modes import mk


def sc(i, title="Surrender", artist="Ueberrest", seconds=210):
    return mk(i, title=title, user={"username": artist, "permalink_url": "https://x"},
              duration=int(seconds * 1000), full_duration=int(seconds * 1000))


class Durations:
    """duration_fn-Fake: feste Sekunden je Dateiname, zählt die Aufrufe."""

    def __init__(self, by_name):
        self.by_name, self.calls = by_name, []

    def __call__(self, path: Path):
        self.calls.append(path.name)
        return self.by_name.get(path.name)


def _lib(tmp_path, *names):
    for n in names:
        p = tmp_path / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")


def test_tolerance_default():
    assert DURATION_TOLERANCE_S == 5.0


def test_same_name_but_different_length_is_not_a_duplicate(tmp_path):
    _lib(tmp_path, "Ueberrest - Surrender.wav")
    fn = Durations({"Ueberrest - Surrender.wav": 390.0})
    coll = Collection(tmp_path, duration_fn=fn)
    assert coll.find_duplicate(sc(1, seconds=210)) is None


def test_same_name_and_similar_length_is_a_duplicate(tmp_path):
    _lib(tmp_path, "Ueberrest - Surrender.wav")
    fn = Durations({"Ueberrest - Surrender.wav": 212.5})
    coll = Collection(tmp_path, duration_fn=fn)
    assert coll.find_duplicate(sc(1, seconds=210)) == "Ueberrest - Surrender.wav"


def test_tolerance_is_configurable(tmp_path):
    _lib(tmp_path, "Ueberrest - Surrender.wav")
    fn = Durations({"Ueberrest - Surrender.wav": 220.0})
    assert Collection(tmp_path, duration_fn=fn).find_duplicate(sc(1)) is None
    assert Collection(tmp_path, duration_fn=fn, duration_tolerance_s=15.0).find_duplicate(sc(1)) is not None


def test_unknown_duration_keeps_the_name_match(tmp_path):
    _lib(tmp_path, "Ueberrest - Surrender.wav")
    assert Collection(tmp_path, duration_fn=Durations({})).find_duplicate(sc(1)) is not None

    def boom(path):
        raise RuntimeError("ffprobe kaputt")

    assert Collection(tmp_path, duration_fn=boom).find_duplicate(sc(1)) is not None


def test_track_without_duration_does_not_probe(tmp_path):
    _lib(tmp_path, "Ueberrest - Surrender.wav")
    fn = Durations({"Ueberrest - Surrender.wav": 999.0})
    coll = Collection(tmp_path, duration_fn=fn)
    assert coll.find_duplicate(sc(1, seconds=0)) is not None
    assert fn.calls == []


def test_duration_fn_none_disables_the_check(tmp_path):
    _lib(tmp_path, "Ueberrest - Surrender.wav")
    assert Collection(tmp_path, duration_fn=None).find_duplicate(sc(1)) is not None


def test_next_candidate_is_tried_when_the_first_has_the_wrong_length(tmp_path):
    _lib(tmp_path, "a/Ueberrest - Surrender (Extended).wav", "b/Ueberrest - Surrender.wav")
    fn = Durations({"Ueberrest - Surrender (Extended).wav": 400.0, "Ueberrest - Surrender.wav": 209.0})
    coll = Collection(tmp_path, duration_fn=fn)
    assert coll.find_duplicate(sc(1, seconds=210)) == "Ueberrest - Surrender.wav"


def test_only_name_candidates_are_probed_and_each_file_once(tmp_path):
    _lib(tmp_path, "Ueberrest - Surrender.wav", "Foo - Completely Different Thing.wav")
    fn = Durations({"Ueberrest - Surrender.wav": 210.0})
    coll = Collection(tmp_path, duration_fn=fn)
    coll.mark_duplicates([sc(1), sc(2), sc(3, title="Nothing Alike", artist="Zed")])
    assert fn.calls == ["Ueberrest - Surrender.wav"]


def test_probe_duration_reads_real_audio(tmp_path):
    wav = tmp_path / "t.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                    str(wav)], check=True)
    assert probe_duration(wav) == pytest.approx(4.0, abs=0.2)


def test_probe_duration_returns_none_for_garbage_and_missing(tmp_path):
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"x")
    assert probe_duration(junk) is None
    assert probe_duration(tmp_path / "gibt-es-nicht.wav") is None


def test_default_collection_still_matches_unreadable_files(tmp_path):
    _lib(tmp_path, "Ueberrest - Surrender.wav")
    assert Collection(tmp_path).find_duplicate(sc(1)) == "Ueberrest - Surrender.wav"
