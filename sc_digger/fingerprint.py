"""Audio-Fingerprinting mit Chromaprint (fpcalc) zur Duplikaterkennung."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import struct
import subprocess

log = logging.getLogger(__name__)

MAX_BER = 0.15              # maximale Bitfehlerrate für „gleiche Aufnahme“
MAX_DURATION_DIFF_S = 3.0   # maximale Dauer-Differenz in Sekunden


@dataclass(frozen=True)
class Fingerprint:
    values: list[int]       # Chromaprint-Rohwerte, vorzeichenlos 32 Bit
    duration: float         # Dauer der Datei in Sekunden (von fpcalc)


def compute_fingerprint(
    path: Path,
    *,
    length_s: int = 120,
    timeout_s: int = 120,
) -> Fingerprint | None:
    """Berechnet einen Chromaprint-Fingerprint per fpcalc.

    subprocess.run(["fpcalc", "-raw", "-json", "-length", str(length_s), str(path)],
    capture_output=True, text=True, timeout=timeout_s). JSON {"duration": float, "fingerprint": [int, ...]}.
    Werte mit & 0xFFFFFFFF normalisieren (ältere fpcalc liefern vorzeichenbehaftet).
    Jeder Fehler (fpcalc fehlt, Exit-Code != 0, kein/unvollständiges JSON, Timeout) ->
    log.warning mit "Fingerprint" im Text, Rückgabe None. Wirft nie.
    """
    try:
        res = subprocess.run(
            ["fpcalc", "-raw", "-json", "-length", str(length_s), str(path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        if res.returncode != 0:
            log.warning(
                "Fingerprint-Berechnung fehlgeschlagen für %s (Exit-Code %d): %s",
                path,
                res.returncode,
                res.stderr,
            )
            return None

        data = json.loads(res.stdout)
        if not isinstance(data, dict):
            log.warning("Fingerprint-JSON kein Objekt für %s: %s", path, res.stdout)
            return None

        if "duration" not in data or "fingerprint" not in data:
            log.warning("Fingerprint-Ausgabe unvollständig für %s: %s", path, res.stdout)
            return None

        raw_fp = data["fingerprint"]
        if not isinstance(raw_fp, list):
            log.warning("Fingerprint-Werte keine Liste für %s", path)
            return None

        duration = float(data["duration"])
        values = [int(v) & 0xFFFFFFFF for v in raw_fp]
        return Fingerprint(values=values, duration=duration)
    except Exception as e:
        log.warning("Fingerprint-Fehler für %s: %s", path, e)
        return None


def bit_error_rate(
    a: list[int],
    b: list[int],
    *,
    max_offset: int = 80,
    min_overlap: int = 50,
) -> float:
    """Anteil unterschiedlicher Bits (0.0 bis 1.0) bei der besten Verschiebung.

    Für jeden Versatz off in -max_offset..+max_offset (in Fingerprint-Werten, ~0,12 s je Wert):
    a ab Index max(off, 0) gegen b ab Index max(-off, 0) über die gemeinsame Länge m;
    nur Versätze mit m >= min_overlap zählen; Fehlerrate = Summe popcount(x ^ y) / (32 * m).
    Rückgabe: Minimum über alle Versätze; gibt es keinen gültigen Versatz: 1.0.
    Werte vorher mit & 0xFFFFFFFF normalisieren. Reines Python (int.bit_count()), kein numpy nötig.
    """
    a_norm = [v & 0xFFFFFFFF for v in a]
    b_norm = [v & 0xFFFFFFFF for v in b]
    len_a = len(a_norm)
    len_b = len(b_norm)

    min_ber = 1.0
    for off in range(-max_offset, max_offset + 1):
        start_a = max(off, 0)
        start_b = max(-off, 0)
        m = min(len_a - start_a, len_b - start_b)
        if m < min_overlap:
            continue
        slice_a = a_norm[start_a : start_a + m]
        slice_b = b_norm[start_b : start_b + m]
        diff_bits = sum((x ^ y).bit_count() for x, y in zip(slice_a, slice_b))
        ber = diff_bits / (32 * m)
        if ber < min_ber:
            min_ber = ber
            if min_ber == 0.0:
                break

    return min_ber


def same_recording(
    a: Fingerprint,
    b: Fingerprint,
    *,
    max_ber: float = MAX_BER,
    max_duration_diff_s: float = MAX_DURATION_DIFF_S,
) -> bool:
    """Zuerst die Dauer: abs(a.duration - b.duration) > max_duration_diff_s -> False, OHNE
    bit_error_rate aufzurufen (billiger Vorfilter, später gegen Hunderte Tracks).
    Sonst: bit_error_rate(a.values, b.values) <= max_ber (Grenze eingeschlossen).
    """
    if abs(a.duration - b.duration) > max_duration_diff_s:
        return False
    return bit_error_rate(a.values, b.values) <= max_ber


def encode_fingerprint(values: list[int]) -> str:
    """Kompakt als ASCII-Text für die Spalte tracks.fingerprint: base64 der Werte als
    vorzeichenlose 32-Bit little-endian (struct "<{n}I"), Werte vorher & 0xFFFFFFFF. [] -> "".
    """
    if not values:
        return ""
    n = len(values)
    raw = struct.pack(f"<{n}I", *(v & 0xFFFFFFFF for v in values))
    return base64.b64encode(raw).decode("ascii")


def decode_fingerprint(text: str) -> list[int]:
    """Umkehrung zu encode_fingerprint; "" -> []."""
    if not text:
        return []
    raw = base64.b64decode(text.encode("ascii"))
    n = len(raw) // 4
    return list(struct.unpack(f"<{n}I", raw))
