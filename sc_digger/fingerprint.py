"""Audio-Fingerprinting mit Chromaprint (fpcalc) zur Duplikaterkennung."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import struct
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sc_digger.db import TrackDB, TrackRecord

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


def find_same_recording(fp: Fingerprint, db: "TrackDB") -> "TrackRecord | None":
    """Holt Kandidaten ausschließlich über db.fingerprint_candidates(fp.duration, MAX_DURATION_DIFF_S)
    und gibt den ersten (nach id) zurück, für den same_recording(fp, <Fingerprint des Kandidaten>) gilt.
    Sonst None. Import von TrackDB/TrackRecord nur unter TYPE_CHECKING (kein Zirkelimport).
    """
    candidates = db.fingerprint_candidates(fp.duration, MAX_DURATION_DIFF_S)
    same_fn = getattr(sys.modules[__name__], "same_recording", same_recording)
    for c in candidates:
        if c.fingerprint and c.fingerprint_duration is not None:
            c_fp = Fingerprint(
                values=decode_fingerprint(c.fingerprint),
                duration=c.fingerprint_duration,
            )
            if same_fn(fp, c_fp):
                return c
    return None


def duplicate_groups(records: list["TrackRecord"]) -> list[list["TrackRecord"]]:
    """Gruppen gleicher Aufnahmen (Größe >= 2). Records ohne fingerprint oder fingerprint_duration
    werden ignoriert. Nach Dauer sortieren und nur Paare vergleichen, deren Dauer höchstens
    MAX_DURATION_DIFF_S auseinanderliegt (Schleife abbrechen, nicht same_recording fragen).
    Vergleich über same_recording (als Modulattribut aufrufen, Tests ersetzen es). Gruppen sind
    transitiv (A~B und B~C -> eine Gruppe). Jede Gruppe nach path sortiert, Gruppen nach dem
    ersten path sortiert.
    """
    valid: list["TrackRecord"] = [
        r for r in records
        if r.fingerprint and r.fingerprint_duration is not None
    ]
    if len(valid) < 2:
        return []

    # Nach Dauer sortieren
    valid.sort(key=lambda r: (r.fingerprint_duration, r.id if r.id is not None else 0))

    fps = [
        Fingerprint(values=decode_fingerprint(r.fingerprint), duration=r.fingerprint_duration)
        for r in valid
    ]

    n = len(valid)
    parent = list(range(n))

    def find(i: int) -> int:
        path = []
        while parent[i] != i:
            path.append(i)
            i = parent[i]
        for node in path:
            parent[node] = i
        return i

    def union(i: int, j: int) -> None:
        root_i = find(i)
        root_j = find(j)
        if root_i != root_j:
            parent[root_i] = root_j

    same_fn = getattr(sys.modules[__name__], "same_recording", same_recording)
    for i in range(n):
        dur_i = valid[i].fingerprint_duration
        for j in range(i + 1, n):
            dur_j = valid[j].fingerprint_duration
            if dur_j - dur_i > MAX_DURATION_DIFF_S:
                break
            if same_fn(fps[i], fps[j]):
                union(i, j)

    # Nach Wurzeln gruppieren
    components: dict[int, list["TrackRecord"]] = {}
    for i in range(n):
        root = find(i)
        components.setdefault(root, []).append(valid[i])

    # Nur Gruppen der Größe >= 2
    groups = [group for group in components.values() if len(group) >= 2]

    # Jede Gruppe nach path sortieren
    for group in groups:
        group.sort(key=lambda r: r.path)

    # Gruppen nach dem ersten path sortiert
    groups.sort(key=lambda g: g[0].path)

    return groups
