"""Zusätzliche Modultests für sc_digger.fingerprint."""
import json
import random
import subprocess
from pathlib import Path

import pytest

from sc_digger import fingerprint as fpm
from sc_digger.fingerprint import (
    Fingerprint,
    bit_error_rate,
    compute_fingerprint,
    decode_fingerprint,
    encode_fingerprint,
    same_recording,
)


def _rand_fp(seed: int, n: int = 200) -> list[int]:
    rng = random.Random(seed)
    return [rng.getrandbits(32) for _ in range(n)]


# ---------------- Symmetrie von bit_error_rate ----------------
@pytest.mark.parametrize(
    "seed_a,seed_b,n_a,n_b",
    [
        (42, 42, 200, 200),     # identisch
        (10, 20, 200, 200),     # unabhängig
        (10, 10, 300, 200),     # a länger als b
        (10, 10, 150, 250),     # b länger als a
        (99, 99, 40, 40),       # unter min_overlap
    ],
)
def test_bit_error_rate_is_symmetric(seed_a, seed_b, n_a, n_b):
    a = _rand_fp(seed_a, n_a)
    b = _rand_fp(seed_b, n_b)
    ber_ab = bit_error_rate(a, b)
    ber_ba = bit_error_rate(b, a)
    assert ber_ab == ber_ba


def test_bit_error_rate_is_symmetric_with_offset_and_flips():
    base = _rand_fp(123, 200)
    # Erzeuge abgeleitete Liste mit Versatz und Bit-Flips
    rng = random.Random(456)
    shifted = [v ^ (1 if rng.random() < 0.05 else 0) for v in base[15:]]
    ber_ab = bit_error_rate(base, shifted)
    ber_ba = bit_error_rate(shifted, base)
    assert ber_ab == ber_ba


def test_bit_error_rate_is_symmetric_with_signed_values():
    a = _rand_fp(77, 100)
    b = [v - 2**32 if v >= 2**31 else v for v in a]
    assert bit_error_rate(a, b) == bit_error_rate(b, a) == 0.0


# ---------------- compute_fingerprint Argument-Weitergabe ----------------
def test_compute_fingerprint_forwards_length_and_timeout(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        out = json.dumps({"duration": 42.5, "fingerprint": [1, 2, 3]})
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(fpm.subprocess, "run", fake_run)
    track_path = tmp_path / "track.mp3"

    res = compute_fingerprint(track_path, length_s=60, timeout_s=45)
    assert res == Fingerprint(values=[1, 2, 3], duration=42.5)

    assert len(calls) == 1
    cmd, kw = calls[0]
    assert cmd == ["fpcalc", "-raw", "-json", "-length", "60", str(track_path)]
    assert kw.get("timeout") == 45
    assert kw.get("capture_output") is True
    assert kw.get("text") is True


# ---------------- Weitere Grenzfälle ----------------
def test_compute_fingerprint_handles_invalid_json_types(monkeypatch, tmp_path, caplog):
    # JSON ist eine Liste statt dict
    monkeypatch.setattr(
        fpm.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess([], 0, stdout=json.dumps([1, 2]), stderr=""),
    )
    assert compute_fingerprint(tmp_path / "t.mp3") is None
    assert "Fingerprint" in caplog.text

    # fingerprint ist keine Liste
    monkeypatch.setattr(
        fpm.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess([], 0, stdout=json.dumps({"duration": 10.0, "fingerprint": "abc"}), stderr=""),
    )
    assert compute_fingerprint(tmp_path / "t.mp3") is None


def test_encode_decode_boundary_values():
    values = [0, 0xFFFFFFFF, 1, 2**31 - 1, 2**31]
    encoded = encode_fingerprint(values)
    decoded = decode_fingerprint(encoded)
    assert decoded == values
