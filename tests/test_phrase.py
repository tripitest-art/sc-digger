"""Unit-Tests für sc_digger/phrase.py (Phrasen-Quantisierung)."""
import math

import pytest

from sc_digger import phrase
from sc_digger.phrase import align_cues, calculate_phrase_grid, quantize_to_phrase

# 150 BPM -> Beat = 0.4 s; 16 Beats = 6.4 s; 32 Beats = 12.8 s.


def test_grid_standard_32_beats():
    grid = calculate_phrase_grid(0.0, 150.0, 40.0)
    assert grid == [0.0, 12.8, 25.6, 38.4]


def test_grid_exakt_bis_trackende():
    # Letzter Punkt trifft die Gesamtdauer genau -> noch enthalten.
    grid = calculate_phrase_grid(0.0, 150.0, 25.6)
    assert grid == [0.0, 12.8, 25.6]


def test_grid_mit_offset_und_kleinerer_phrase():
    grid = calculate_phrase_grid(3.2, 150.0, 20.0, phrase_beats=8)
    assert grid == [3.2, 6.4, 9.6, 12.8, 16.0, 19.2]


def test_grid_first_kick_nach_trackende_ist_leer():
    assert calculate_phrase_grid(50.0, 150.0, 40.0) == []


def test_grid_ungueltige_werte_sind_leer():
    assert calculate_phrase_grid(0.0, 0.0, 100.0) == []
    assert calculate_phrase_grid(0.0, -150.0, 100.0) == []
    assert calculate_phrase_grid(0.0, 150.0, 0.0) == []
    assert calculate_phrase_grid(-1.0, 150.0, 100.0) == []
    assert calculate_phrase_grid(0.0, 150.0, 100.0, phrase_beats=0) == []


def test_grid_nicht_endliche_werte_sind_leer():
    assert calculate_phrase_grid(float("nan"), 150.0, 100.0) == []
    assert calculate_phrase_grid(0.0, float("inf"), 100.0) == []


def test_quantize_rastet_auf_naechste_32_beat_grenze():
    # 12.4 s = 31 Beats -> 1 Beat unter der 32-Beat-Grenze -> 12.8 s.
    assert quantize_to_phrase(12.4, 0.0, 150.0) == pytest.approx(12.8, abs=0.001)
    # 13.1 s = 32.75 Beats -> 0.75 Beats über der Grenze -> 12.8 s.
    assert quantize_to_phrase(13.1, 0.0, 150.0) == pytest.approx(12.8, abs=0.001)


def test_quantize_auf_erster_grenze_bleibt_erster_kick():
    # 0.5 s = 1.25 Beats -> innerhalb der Toleranz von 0.0 s.
    assert quantize_to_phrase(0.5, 0.0, 150.0) == pytest.approx(0.0, abs=0.001)


def test_quantize_rastet_auf_sekundaere_16_beat_grenze():
    # 19.0 s = 47.5 Beats; 32-Beat-Grenze (12.8 s) zu weit weg, 48 Beats = 19.2 s passt.
    assert quantize_to_phrase(19.0, 0.0, 150.0) == pytest.approx(19.2, abs=0.001)


def test_quantize_fallback_auf_taktanfang_ohne_downbeats():
    # 14.35 s = 35.875 Beats: außerhalb beider Fenster -> nächster 4-Beat-Takt = 36 Beats = 14.4 s.
    assert quantize_to_phrase(14.35, 0.0, 150.0) == pytest.approx(14.4, abs=0.001)


def test_quantize_fallback_auf_downbeat_bevorzugt_echten_downbeat():
    # 2-Beat-Einschub: 13.55 s ist 1.875 Beats von der 32-Beat-Grenze (12.8 s) entfernt,
    # also außerhalb der Toleranz. Der echte Downbeat 13.6 s ist näher als 12.8 s.
    dbs = [0.0, 6.4, 12.8, 13.6, 15.2, 16.8]
    assert quantize_to_phrase(13.55, 0.0, 150.0, downbeats=dbs) == pytest.approx(13.6, abs=0.001)


def test_quantize_fallback_ignoriert_leere_downbeat_liste():
    # Leere Downbeat-Liste verhält sich wie „keine Downbeats“ -> Taktanfang.
    assert quantize_to_phrase(14.35, 0.0, 150.0, downbeats=[]) == pytest.approx(14.4, abs=0.001)


def test_quantize_respektiert_first_kick_offset():
    # first_kick 2.0 s; Event 5.0 s = 7.5 Beats -> 8 Beats = 5.2 s (32-Beat-Grenze bei 14.8 s).
    assert quantize_to_phrase(5.0, 2.0, 150.0) == pytest.approx(5.2, abs=0.001)


def test_quantize_kantenfaelle_bleiben_unveraendert():
    # Event vor dem ersten Kick.
    assert quantize_to_phrase(2.0, 5.0, 150.0) == 2.0
    # Ungültige BPM.
    assert quantize_to_phrase(10.0, 0.0, 0.0) == 10.0
    assert quantize_to_phrase(10.0, 0.0, -120.0) == 10.0
    # Negativer erster Kick.
    assert quantize_to_phrase(10.0, -1.0, 150.0) == 10.0


def test_quantize_nicht_endliche_werte_werden_durchgereicht():
    assert math.isnan(quantize_to_phrase(float("nan"), 0.0, 150.0))
    assert math.isinf(quantize_to_phrase(float("inf"), 0.0, 150.0))


def test_align_cues_quantisiert_und_laesst_first_kick_und_none():
    cues = {
        "first_kick": 0.0,
        "drop": 12.4,
        "breakdown": None,
        "outro": 38.3,
    }
    aligned = align_cues(cues, 0.0, 150.0)
    assert aligned["first_kick"] == 0.0
    assert aligned["drop"] == pytest.approx(12.8, abs=0.001)
    assert aligned["breakdown"] is None
    assert aligned["outro"] == pytest.approx(38.4, abs=0.001)
    # Neues Dictionary, Original unverändert.
    assert aligned is not cues
    assert cues["drop"] == 12.4


def test_align_cues_reicht_downbeats_durch():
    cues = {"drop": 13.55}
    dbs = [0.0, 6.4, 12.8, 13.6, 15.2, 16.8]
    aligned = align_cues(cues, 0.0, 150.0, downbeats=dbs)
    assert aligned["drop"] == pytest.approx(13.6, abs=0.001)


def test_cli_gibt_quantisierten_wert_aus(capsys):
    assert phrase.main(["--event", "12.4", "--first-kick", "0.0", "--bpm", "150.0"]) == 0
    assert capsys.readouterr().out.strip() == "12.800 s"


def test_cli_mit_eigenen_gitteroptionen(capsys):
    ret = phrase.main([
        "--event", "19.0", "--first-kick", "0.0", "--bpm", "150.0",
        "--secondary-phrase-beats", "16", "--tolerance-beats", "1.75",
    ])
    assert ret == 0
    assert capsys.readouterr().out.strip() == "19.200 s"


def test_cli_ungueltige_oder_fehlende_argumente(capsys):
    assert phrase.main([]) == 1
    assert phrase.main(["--event", "abc", "--first-kick", "0.0", "--bpm", "150.0"]) == 1
    assert phrase.main(["--event", "12.4", "--first-kick", "0.0"]) == 1
    capsys.readouterr()
