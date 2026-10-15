"""Eigene Tests zur Dauer-Prüfung im Sammlungs-Abgleich (Issue #291)."""
from pathlib import Path

import pytest

import sc_digger.collection as collection
from sc_digger.collection import (
    DURATION_TOLERANCE_S,
    Collection,
    probe_duration,
)
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


def test_tolerance_boundary_is_exactly_the_limit(tmp_path):
    _lib(tmp_path, "Ueberrest - Surrender.wav")
    # 5,0 s Abweichung gilt noch als dasselbe Stück, 5,1 s nicht mehr
    at_limit = Durations({"Ueberrest - Surrender.wav": 215.0})
    above_limit = Durations({"Ueberrest - Surrender.wav": 215.1})
    assert Collection(tmp_path, duration_fn=at_limit).find_duplicate(sc(1)) == "Ueberrest - Surrender.wav"
    assert Collection(tmp_path, duration_fn=above_limit).find_duplicate(sc(1)) is None
    assert DURATION_TOLERANCE_S == 5.0


def test_shorter_file_is_rejected_as_well(tmp_path):
    _lib(tmp_path, "Ueberrest - Surrender.wav")
    fn = Durations({"Ueberrest - Surrender.wav": 200.0})
    assert Collection(tmp_path, duration_fn=fn).find_duplicate(sc(1, seconds=210)) is None


def test_reload_clears_the_duration_cache(tmp_path):
    _lib(tmp_path, "Ueberrest - Surrender.wav")
    fn = Durations({"Ueberrest - Surrender.wav": 210.0})
    coll = Collection(tmp_path, duration_fn=fn)
    assert coll.find_duplicate(sc(1)) == "Ueberrest - Surrender.wav"
    assert fn.calls == ["Ueberrest - Surrender.wav"]
    coll.reload()
    assert coll.find_duplicate(sc(1)) == "Ueberrest - Surrender.wav"
    assert fn.calls == ["Ueberrest - Surrender.wav", "Ueberrest - Surrender.wav"]


def test_two_candidates_both_too_long_give_none(tmp_path):
    _lib(tmp_path, "Ueberrest - Surrender.wav", "b/Ueberrest - Surrender (Extended).wav")
    fn = Durations({"Ueberrest - Surrender.wav": 390.0,
                    "Ueberrest - Surrender (Extended).wav": 400.0})
    coll = Collection(tmp_path, duration_fn=fn)
    assert coll.find_duplicate(sc(1, seconds=210)) is None
    # beide Kandidaten wurden geprüft, keiner doppelt
    assert sorted(fn.calls) == ["Ueberrest - Surrender (Extended).wav", "Ueberrest - Surrender.wav"]


def test_probe_duration_never_raises_on_odd_probe_output(monkeypatch):
    # ffprobe liefert nicht für jedes Format eine Dauer -> unbekannt statt Absturz
    monkeypatch.setattr(collection, "quality_probe", lambda path: {"streams": [{"codec_name": "pcm_s16le"}]})
    assert probe_duration(Path("/egal.wav")) is None

    monkeypatch.setattr(collection, "quality_probe", lambda path: {"format": {"duration": "0.000000"}})
    assert probe_duration(Path("/egal.wav")) is None

    monkeypatch.setattr(collection, "quality_probe", lambda path: {"format": {"duration": "213.5"}})
    assert probe_duration(Path("/egal.wav")) == pytest.approx(213.5)
