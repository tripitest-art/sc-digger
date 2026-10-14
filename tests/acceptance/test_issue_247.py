"""Akzeptanztests: Phrasen-Quantisierung (16/32-Beat Alignment)."""
import math
import pytest

from sc_digger import phrase
from sc_digger.phrase import calculate_phrase_grid, quantize_to_phrase, align_cues


def test_calculate_phrase_grid_32_beats():
    # 150 BPM -> Beat = 0.4s, 32 Beats = 12.8s
    grid = calculate_phrase_grid(first_kick_s=0.0, bpm=150.0, total_duration_s=40.0, phrase_beats=32)
    assert grid == [0.0, 12.8, 25.6, 38.4]


def test_calculate_phrase_grid_mit_offset():
    # 150 BPM, first_kick bei 6.4s, 16 Beats = 6.4s
    grid = calculate_phrase_grid(first_kick_s=6.4, bpm=150.0, total_duration_s=25.0, phrase_beats=16)
    assert grid == [6.4, 12.8, 19.2]


def test_calculate_phrase_grid_ungueltige_werte():
    assert calculate_phrase_grid(0.0, 0.0, 100.0) == []
    assert calculate_phrase_grid(0.0, -120.0, 100.0) == []
    assert calculate_phrase_grid(0.0, 150.0, -10.0) == []
    assert calculate_phrase_grid(-5.0, 150.0, 100.0) == []


def test_quantize_to_phrase_32_beats_innerhalb_toleranz():
    # 150 BPM -> Beat = 0.4s. 32 Beats = 12.8s, Toleranz 1.75 Beats = 0.7s.
    # Event bei 12.4s (Versatz -0.4s = -1.0 Beat) -> rastet auf 12.8s ein.
    res = quantize_to_phrase(12.4, first_kick_s=0.0, bpm=150.0)
    assert res == pytest.approx(12.8, abs=0.001)

    # Event bei 13.1s (Versatz +0.3s = +0.75 Beats) -> rastet auf 12.8s ein.
    res2 = quantize_to_phrase(13.1, first_kick_s=0.0, bpm=150.0)
    assert res2 == pytest.approx(12.8, abs=0.001)


def test_quantize_to_phrase_16_beats_sekundaer():
    # 150 BPM -> 16 Beats = 6.4s, 32 Beats = 12.8s, 48 Beats = 19.2s (16-Beat-Grenze, nicht 32).
    # Event bei 19.0s (0.2s vor 19.2s = 0.5 Beats) -> rastet auf 19.2s ein.
    res = quantize_to_phrase(19.0, first_kick_s=0.0, bpm=150.0)
    assert res == pytest.approx(19.2, abs=0.001)


def test_quantize_to_phrase_fallback_downbeat_ohne_downbeat_liste():
    # 150 BPM -> Beat = 0.4s, Bar (4 Beats) = 1.6s.
    # Phrasengrenze bei 12.8s. Event bei 14.4s (Versatz 1.6s = 4 Beats > 1.75 Toleranz).
    # Nächster 4-Beat-Takt zu 14.4s ist 14.4s selbst (9. Takt).
    res = quantize_to_phrase(14.35, first_kick_s=0.0, bpm=150.0)
    assert res == pytest.approx(14.4, abs=0.001)


def test_quantize_to_phrase_fallback_mit_downbeats_ungerader_einschub():
    # Track mit 2-Beat-Pause: Downbeats liegen bei [..., 12.8, 13.6, 15.2, ...]
    # Drop bei 13.55s (13.6 ist 2 Beats nach 12.8s Phrasengrenze -> außerhalb 1.75 Beat Toleranz).
    dbs = [0.0, 6.4, 12.8, 13.6, 15.2, 16.8]
    res = quantize_to_phrase(13.55, first_kick_s=0.0, bpm=150.0, downbeats=dbs)
    # Rastet auf 13.6s (den echten Downbeat) ein, NICHT zurück auf 12.8s!
    assert res == pytest.approx(13.6, abs=0.001)


def test_quantize_to_phrase_kantenfaelle():
    # Event vor erstem Kick
    assert quantize_to_phrase(2.0, first_kick_s=5.0, bpm=150.0) == 2.0
    # Ungültige BPM
    assert quantize_to_phrase(10.0, first_kick_s=0.0, bpm=0.0) == 10.0
    assert quantize_to_phrase(10.0, first_kick_s=0.0, bpm=-120.0) == 10.0
    # first_kick_s negativ
    assert quantize_to_phrase(10.0, first_kick_s=-1.0, bpm=150.0) == 10.0


def test_align_cues():
    cues_in = {
        "first_kick": 0.0,
        "drop": 12.4,          # soll auf 12.8s einrasten
        "breakdown": None,     # bleibt None
        "outro": 38.3,         # soll auf 38.4s einrasten (3 * 32 Beats = 38.4s)
    }
    aligned = align_cues(cues_in, first_kick_s=0.0, bpm=150.0)
    assert aligned["first_kick"] == 0.0
    assert aligned["drop"] == pytest.approx(12.8, abs=0.001)
    assert aligned["breakdown"] is None
    assert aligned["outro"] == pytest.approx(38.4, abs=0.001)
    # Original-Dictionary unverändert
    assert cues_in["drop"] == 12.4


def test_cli_aufruf(capsys):
    ret = phrase.main(["--event", "12.4", "--first-kick", "0.0", "--bpm", "150.0"])
    assert ret == 0
    out = capsys.readouterr().out.strip()
    assert out == "12.800 s"


def test_cli_ungueltige_argumente(capsys):
    assert phrase.main([]) == 1
    assert phrase.main(["--event", "abc"]) == 1
