"""Tests der fusionierten, numba-freien Trackanalyse (Issue #283).

Deckt ab, was die Akzeptanztests nicht abdecken: exakter Rückgabe-Vertrag,
Konfidenzwerte, die ACF-Bedingung des Oktav-Snappings, numba-Freiheit und das
Verhalten bei defekten Dateien. Alles ohne Netz, Audio synthetisch per ffmpeg.
"""
from __future__ import annotations

import builtins
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np

from sc_digger import analysis as A

_CAMELOT_RE = re.compile(r"(?:[1-9]|1[0-2])[AB]")


def _synthetischer_track(pfad: Path, *, freq: float = 440.0, bpm: float = 155.0,
                         dauer: int = 15) -> Path:
    """Tremolo-Sinus als WAV: den Puls gibt die Amplitudenmodulation vor."""
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
        "-i", f"sine=frequency={freq}:duration={dauer},tremolo=f={bpm / 60.0}:d=0.9",
        "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", str(pfad),
    ], check=True)
    return pfad


def _klickzug(bpm: float, *, dauer: float = 16.0) -> np.ndarray:
    """Klickfolge in numpy: ein echter Pulszug ohne Tremolo-Mehrdeutigkeit."""
    y = np.zeros(int(A.SR * dauer), dtype=np.float32)
    schritt = int(round(A.SR * 60.0 / bpm))
    for i in range(0, y.size, schritt):
        y[i:i + 400] = 0.9
    return y


def _tremolo(bpm: float, *, freq: float = 440.0, dauer: float = 15.0) -> np.ndarray:
    t = np.arange(int(A.SR * dauer), dtype=np.float64) / A.SR
    huelle = 0.55 + 0.45 * np.sin(2 * np.pi * (bpm / 60.0) * t)
    return (np.sin(2 * np.pi * freq * t) * huelle).astype(np.float32)


def test_analyze_track_liefert_genau_die_dokumentierten_felder(tmp_path):
    res = A.analyze_track(_synthetischer_track(tmp_path / "felder.wav"))
    assert set(res) == {"bpm", "bpm_confidence", "bpm_octave", "key_camelot",
                        "key_name", "key_confidence", "key_margin"}
    assert isinstance(res["bpm"], float)
    assert isinstance(res["bpm_octave"], int)
    assert 0.0 <= res["bpm_confidence"] <= 1.0
    assert 0.0 <= res["key_confidence"] <= 1.0
    assert isinstance(res["key_margin"], float)
    assert _CAMELOT_RE.fullmatch(res["key_camelot"])
    assert res["key_name"] in A._KEY_NAMES_MAJOR + A._KEY_NAMES_MINOR


def test_analyze_track_dekodiert_genau_einmal_auch_bei_kurzer_datei(tmp_path):
    # 4 s liegt unter dem 5-s-Fenster, in dem _decode_audio früher ein zweites
    # Mal dekodiert hat. Der Analysepfad darf trotzdem nur einen Aufruf machen.
    kurz = _synthetischer_track(tmp_path / "kurz.wav", dauer=4)
    with patch("subprocess.run", wraps=subprocess.run) as mock_run:
        res = A.analyze_track(kurz)
    calls = [c for c in mock_run.call_args_list
             if c[0] and isinstance(c[0][0], list) and "ffmpeg" in c[0][0][0]]
    assert len(calls) == 1
    assert res["bpm"] is not None
    assert res["key_camelot"] is not None


def test_analyze_track_want_flags_steuern_die_auswertung(tmp_path):
    track = _synthetischer_track(tmp_path / "flags.wav")
    ohne_bpm = A.analyze_track(track, want_bpm=False)
    assert ohne_bpm["bpm"] is None and ohne_bpm["bpm_confidence"] == 0.0
    assert ohne_bpm["bpm_octave"] == 1
    assert ohne_bpm["key_camelot"] is not None
    ohne_key = A.analyze_track(track, want_key=False)
    assert ohne_key["bpm"] is not None
    assert ohne_key["key_camelot"] is None and ohne_key["key_confidence"] == 0.0


def test_detect_tempo_konfidenz_trennt_pulszug_von_stille():
    klar = A.detect_tempo(_klickzug(155.0))
    assert abs(klar.bpm - 155.0) <= 1.0
    assert klar.confidence > 0.7
    stille = A.detect_tempo(np.zeros(A.SR * 16, dtype=np.float32))
    assert stille.bpm is None
    assert stille.confidence == 0.0


def test_snap_to_rastet_halbzeit_ein_aber_nicht_gegen_den_pulszug():
    # Halbzeit-Material: Ein Tremolo erzeugt zwei Flux-Spitzen pro Schlagzyklus,
    # der doppelte Wert wird vom Pulszug also gestützt und darf einrasten.
    tremolo = A.detect_tempo(_tremolo(79.0))
    assert abs(tremolo.bpm - 79.0) <= 2.0
    assert tremolo.octave == 1
    gesnappt = A.detect_tempo(_tremolo(79.0), snap_to=(150.0, 165.0))
    assert 150.0 <= gesnappt.bpm <= 165.0
    assert gesnappt.octave == 2

    # Echter Pulszug bei 78 BPM: Hier stützt der Pulszug das Doppelte nicht,
    # also bleibt der Wert stehen. Snapping ist kein Umbiegen ins Fenster.
    echt = A.detect_tempo(_klickzug(78.0), snap_to=(150.0, 165.0))
    assert abs(echt.bpm - 78.0) <= 1.0
    assert echt.octave == 1


def test_chroma_ordnet_einen_kammerton_der_richtigen_pitch_class_zu():
    # 440 Hz = A4 = Pitch-Class 9: Das oktav-gemittelte Chroma muss dort sein
    # Maximum haben; ein lineares FFT-Bin-Raster ordnet das gern falsch zu.
    y = _tremolo(155.0, dauer=3.0)
    ch, w = A.chroma(A.resample(y, A.SR, A.SR_CHROMA))
    assert ch.shape[0] == 12
    assert int(np.argmax(np.average(ch, axis=1, weights=w))) == 9
    assert A._detect_key_from_audio(y)["key_name"] == "A"


def test_analysepfad_braucht_weder_librosa_noch_numba(tmp_path):
    # Der Kern existiert, um ohne numba-JIT zu laufen: Sind die Importe
    # blockiert, muss die Erkennung trotzdem Werte liefern.
    track = _synthetischer_track(tmp_path / "ohne_numba.wav")
    echt_import = builtins.__import__

    def ohne_numba(name, *args, **kwargs):
        if name.split(".")[0] in {"librosa", "numba"}:
            raise ImportError(f"{name} für diesen Test blockiert")
        return echt_import(name, *args, **kwargs)

    with patch("builtins.__import__", ohne_numba):
        res = A.analyze_track(track)
        bpm = A.detect_bpm(track)
        key = A.detect_key(track)
    assert res["bpm"] is not None and abs(res["bpm"] - 155.0) <= 2.0
    assert bpm is not None and abs(bpm - 155.0) <= 2.0
    # A-Dur ist 11B auf dem Camelot-Rad (Parallele F#m = 11A). Die frühere Erwartung "1B" hielt
    # einen Fehler der Dur-Tabelle fest (F#, G, G#, A, A#, B waren falsch belegt).
    assert key == ("11B", "A")


def test_defekte_oder_fehlende_datei_liefert_none_statt_ausnahme(tmp_path):
    kaputt = tmp_path / "kaputt.wav"
    kaputt.write_bytes(b"das ist kein audio")
    assert A.detect_bpm(kaputt) is None
    assert A.detect_key(kaputt) is None
    leer = A.analyze_track(kaputt)
    assert leer["bpm"] is None and leer["key_camelot"] is None
    fehlt = A.analyze_track(tmp_path / "gibtsnicht.wav")
    assert fehlt["bpm"] is None and fehlt["key_camelot"] is None
