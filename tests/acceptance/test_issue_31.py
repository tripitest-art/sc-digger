"""Akzeptanztests: Audio-Fingerprints (Chromaprint) vergleichen, ohne fpcalc im Test."""
import json
import random
import subprocess
from pathlib import Path

import pytest

from sc_digger import fingerprint as fpm
from sc_digger.fingerprint import (MAX_BER, MAX_DURATION_DIFF_S, Fingerprint, bit_error_rate,
                                   compute_fingerprint, decode_fingerprint, encode_fingerprint,
                                   same_recording)


def rand_fp(seed: int, n: int = 700) -> list[int]:
    rng = random.Random(seed)
    return [rng.getrandbits(32) for _ in range(n)]


def flip(values: list[int], p: float, seed: int) -> list[int]:
    """Kippt jedes Bit mit Wahrscheinlichkeit p (simuliert ein anderes Encoding)."""
    rng = random.Random(seed)
    return [v ^ sum(1 << b for b in range(32) if rng.random() < p) for v in values]


A = rand_fp(1)


def test_constants_from_measurement():
    # Messung mit fpcalc 1.5.1: gleiche Aufnahme 0,000–0,05, Extended-Edit 0,12, andere Tracks ≥ 0,28
    assert MAX_BER == 0.15
    assert MAX_DURATION_DIFF_S == 3.0


# ---------------- Bitfehlerrate ----------------
def test_identical_is_zero():
    assert bit_error_rate(A, list(A)) == 0.0


def test_other_encoding_is_small():
    assert 0.01 < bit_error_rate(A, flip(A, 0.02, seed=2)) < 0.03


def test_offset_within_window_is_found():
    assert bit_error_rate(A, A[20:]) == 0.0      # Start 20 Werte (~2,5 s) später
    assert bit_error_rate(A[35:], A) == 0.0


def test_offset_outside_window_is_not_found():
    assert bit_error_rate(A, A[100:]) > 0.3      # max_offset=80


def test_unrelated_is_about_half():
    assert 0.45 < bit_error_rate(A, rand_fp(99)) < 0.55


def test_too_little_overlap_is_one():
    assert bit_error_rate(A[:40], A[:40]) == 1.0  # min_overlap=50
    assert bit_error_rate([], A) == 1.0


def test_signed_values_are_treated_as_unsigned():
    signed = [v - 2**32 if v >= 2**31 else v for v in A]
    assert any(v < 0 for v in signed)
    assert bit_error_rate(A, signed) == 0.0


# ---------------- Gleiche Aufnahme? ----------------
def test_same_recording_needs_similar_audio_and_duration():
    fp = Fingerprint(A, 90.0)
    assert same_recording(fp, Fingerprint(flip(A, 0.02, seed=3), 90.4))
    assert same_recording(fp, Fingerprint(A, 92.9))              # < 3 s Unterschied
    assert not same_recording(fp, Fingerprint(A, 93.1))          # > 3 s: anderer Edit
    assert not same_recording(fp, Fingerprint(A, 60.0))          # Radio-Edit: Fingerprint gleich, Dauer nicht
    assert not same_recording(fp, Fingerprint(rand_fp(7), 90.0)) # anderer Track


def test_same_recording_checks_duration_before_comparing(monkeypatch):
    calls = []
    monkeypatch.setattr(fpm, "bit_error_rate", lambda *a, **k: calls.append(1) or 0.0)
    assert not same_recording(Fingerprint(A, 90.0), Fingerprint(A, 200.0))
    assert calls == []   # teurer Vergleich nur, wenn die Dauer passt


def test_threshold_is_inclusive(monkeypatch):
    monkeypatch.setattr(fpm, "bit_error_rate", lambda *a, **k: MAX_BER)
    assert same_recording(Fingerprint(A, 90.0), Fingerprint(A, 90.0))


# ---------------- Speichern als Text (Spalte tracks.fingerprint) ----------------
def test_encode_decode_roundtrip():
    s = encode_fingerprint(A)
    assert isinstance(s, str) and s.isascii() and "," not in s
    assert decode_fingerprint(s) == A
    assert len(s) < len(",".join(map(str, A)))    # kompakter als eine Zahlenliste


def test_encode_normalizes_signed_and_handles_empty():
    assert decode_fingerprint(encode_fingerprint([-1, 0])) == [0xFFFFFFFF, 0]
    assert encode_fingerprint([]) == "" and decode_fingerprint("") == []


# ---------------- fpcalc aufrufen ----------------
def _fake_run(result):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        if isinstance(result, BaseException):
            raise result
        return result
    return run, calls


def test_compute_fingerprint_parses_fpcalc_json(monkeypatch, tmp_path):
    out = json.dumps({"duration": 90.0, "fingerprint": A[:5]})
    run, calls = _fake_run(subprocess.CompletedProcess([], 0, stdout=out, stderr=""))
    monkeypatch.setattr(fpm.subprocess, "run", run)
    p = tmp_path / "a.wav"
    assert compute_fingerprint(p) == Fingerprint(A[:5], 90.0)
    cmd, kw = calls[0]
    assert cmd == ["fpcalc", "-raw", "-json", "-length", "120", str(p)]
    assert kw.get("timeout") and kw.get("capture_output") is True


@pytest.mark.parametrize("result", [
    subprocess.CompletedProcess([], 2, stdout="", stderr="ERROR: Could not open the input file"),
    subprocess.CompletedProcess([], 0, stdout="kein json", stderr=""),
    subprocess.CompletedProcess([], 0, stdout=json.dumps({"duration": 1.0}), stderr=""),
    FileNotFoundError("fpcalc"),
    subprocess.TimeoutExpired("fpcalc", 120),
])
def test_compute_fingerprint_never_raises(monkeypatch, tmp_path, result, caplog):
    run, _ = _fake_run(result)
    monkeypatch.setattr(fpm.subprocess, "run", run)
    assert compute_fingerprint(tmp_path / "a.wav") is None
    assert "Fingerprint" in caplog.text or "fpcalc" in caplog.text
