"""Phrasen-Quantisierung: 16/32-Beat-Alignment von Cues relativ zum ersten Kick.

Reine Zeit- und Beat-Arithmetik, keine Audio-Dekodierung. Berechnet aus
`first_kick` und BPM ein Phrasengitter und zieht Sektions-Cues (Drop,
Breakdown, Outro) mit einem Toleranzfenster auf Phrasen-Downbeats. Liegt ein
Cue außerhalb des Toleranzfensters (z. B. ungerade Pause oder 2-Beat-Fill),
wird auf übergebene Downbeats bzw. auf 4-Beat-Taktanfänge zurückgefallen.
"""
from __future__ import annotations

import argparse
import math
import sys
from typing import Sequence

# Obergrenze für die Zahl der Gitterpunkte. Schutz gegen Endlosschleifen bei
# pathologisch kleinen Phrasenlängen (bpm extrem groß); für reale Tracks liegt
# die Zahl der Phrasen um mehrere Größenordnungen darunter.
_MAX_GRID_POINTS = 1_000_000


def _round3(x: float) -> float:
    """Rundet auf 3 Nachkommastellen (Zeitauflösung in Millisekunden)."""
    return round(x, 3)


def _is_finite(x: float) -> bool:
    """True, wenn x eine endliche Zahl ist (kein NaN, kein +/-inf, kein Nicht-Zahl-Typ)."""
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _snap_to_grid(offset_beats: float, grid_beats: int, tolerance_beats: float) -> float | None:
    """Nächstes Vielfaches von grid_beats, wenn offset_beats innerhalb der Toleranz liegt.

    Gibt den gerasteten Beat-Abstand zurück, sonst None. Ungültige Gitterbreiten
    (<= 0) liefern None.
    """
    if grid_beats <= 0:
        return None
    k = round(offset_beats / grid_beats)
    grid = k * grid_beats
    if abs(offset_beats - grid) <= tolerance_beats:
        return grid
    return None


def calculate_phrase_grid(
    first_kick_s: float,
    bpm: float,
    total_duration_s: float,
    *,
    phrase_beats: int = 32,
) -> list[float]:
    """Berechnet die Zeitpunkte (Sekunden, auf 3 Nachkommastellen gerundet) des Phrasengitters
    ausgehend von first_kick_s bis total_duration_s.
    - phrase_beats: Beats pro Phrase (Standard 32 = 8 Takte à 4 Beats).
    - Erstes Element ist first_kick_s (falls <= total_duration_s).
    - Gibt [] zurück bei bpm <= 0, total_duration_s <= 0 oder first_kick_s < 0.
    - Wirft nie.
    """
    if not (_is_finite(first_kick_s) and _is_finite(bpm) and _is_finite(total_duration_s)):
        return []
    if bpm <= 0 or total_duration_s <= 0 or first_kick_s < 0 or phrase_beats <= 0:
        return []

    beat_s = 60.0 / bpm
    step_s = phrase_beats * beat_s

    grid: list[float] = []
    k = 0
    while k < _MAX_GRID_POINTS:
        t = first_kick_s + k * step_s
        if t > total_duration_s:
            break
        grid.append(_round3(t))
        k += 1
    return grid


def quantize_to_phrase(
    event_s: float,
    first_kick_s: float,
    bpm: float,
    *,
    phrase_beats: int = 32,
    secondary_phrase_beats: int = 16,
    tolerance_beats: float = 1.75,
    downbeats: Sequence[float] | None = None,
) -> float:
    """Quantisiert einen Event-Zeitpunkt (Sekunden) auf das Phrasengitter relativ zu first_kick_s.
    - Beat-Dauer = 60.0 / bpm.
    - Primäres Gitter: Vielfache von phrase_beats (Standard 32 Beats). Liegt event_s innerhalb
      von ±(tolerance_beats * beat_s) um eine 32-Beat-Grenze, rastet es exakt auf diese Grenze ein.
    - Sekundäres Gitter: Vielfache von secondary_phrase_beats (Standard 16 Beats). Liegt event_s
      innerhalb von ±(tolerance_beats * beat_s) um eine 16-Beat-Grenze, rastet es darauf ein.
    - Fallback: Liegt event_s außerhalb beider Toleranzfenster (z. B. ungerade Pause / 2-Beat-Fill):
      - Wenn downbeats übergeben wurden: rastet auf den zeitlich nächsten Downbeat ein.
      - Ohne downbeats: rastet auf den nächsten 4-Beat-Taktanfang (first_kick_s + k * 4 * beat_s) ein.
    - Ergebnis wird auf 3 Nachkommastellen gerundet.
    - Kantenfälle: Bei event_s < first_kick_s, bpm <= 0, first_kick_s < 0 oder ungültigen/nicht-endlichen
      Zahlen wird round(event_s, 3) unverändert zurückgegeben. Wirft nie.
    """
    if not (
        _is_finite(event_s)
        and _is_finite(first_kick_s)
        and _is_finite(bpm)
        and _is_finite(tolerance_beats)
    ):
        return _round3(event_s)
    if bpm <= 0 or first_kick_s < 0 or event_s < first_kick_s:
        return _round3(event_s)

    beat_s = 60.0 / bpm
    offset_beats = (event_s - first_kick_s) / beat_s

    snap = _snap_to_grid(offset_beats, phrase_beats, tolerance_beats)
    if snap is None:
        snap = _snap_to_grid(offset_beats, secondary_phrase_beats, tolerance_beats)
    if snap is not None:
        return _round3(first_kick_s + snap * beat_s)

    if downbeats is not None:
        valid = [float(d) for d in downbeats if _is_finite(d)]
        if valid:
            nearest = min(valid, key=lambda d: abs(d - event_s))
            return _round3(nearest)

    bar_beats = 4.0
    bar_index = round(offset_beats / bar_beats)
    return _round3(first_kick_s + bar_index * bar_beats * beat_s)


def align_cues(
    cues: dict[str, float | None],
    first_kick_s: float,
    bpm: float,
    *,
    phrase_beats: int = 32,
    secondary_phrase_beats: int = 16,
    tolerance_beats: float = 1.75,
    downbeats: Sequence[float] | None = None,
) -> dict[str, float | None]:
    """Richtet ein Dictionary von Cue-Zeitpunkten (z. B. {"first_kick": ..., "drop": ..., "breakdown": ..., "outro": ...})
    an first_kick_s und dem Phrasenraster aus.
    - 'first_kick' bleibt unverändert.
    - Alle anderen Einträge mit float-Wert werden per quantize_to_phrase quantisiert.
    - None-Werte bleiben None.
    - Gibt ein neues Dictionary zurück. Wirft nie.
    """
    aligned: dict[str, float | None] = {}
    for name, value in cues.items():
        if name == "first_kick" or value is None or not isinstance(value, (int, float)):
            aligned[name] = value
            continue
        aligned[name] = quantize_to_phrase(
            value,
            first_kick_s,
            bpm,
            phrase_beats=phrase_beats,
            secondary_phrase_beats=secondary_phrase_beats,
            tolerance_beats=tolerance_beats,
            downbeats=downbeats,
        )
    return aligned


def main(argv: list[str] | None = None) -> int:
    """Kommandozeile: python -m sc_digger.phrase --event <sekunden> --first-kick <sekunden> --bpm <bpm>
    Gibt den quantisierten Wert auf stdout aus: f"{quantized:.3f} s"
    Rückgabe 0 bei Erfolg, 1 bei ungültigen oder fehlenden Argumenten.
    """
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="sc_digger.phrase",
        description="Ein Cue-Zeitpunkt auf das 16/32-Beat-Phrasengitter quantisieren.",
    )
    parser.add_argument("--event", type=float, required=True, help="Event-Zeitpunkt in Sekunden")
    parser.add_argument("--first-kick", dest="first_kick", type=float, required=True,
                        help="Zeitpunkt des ersten Kicks in Sekunden")
    parser.add_argument("--bpm", type=float, required=True, help="Tempo des Tracks")
    parser.add_argument("--phrase-beats", dest="phrase_beats", type=int, default=32,
                        help="Beats pro Phrase (Standard 32)")
    parser.add_argument("--secondary-phrase-beats", dest="secondary_phrase_beats", type=int,
                        default=16, help="Beats pro sekundärer Phrase (Standard 16)")
    parser.add_argument("--tolerance-beats", dest="tolerance_beats", type=float, default=1.75,
                        help="Toleranzfenster in Beats (Standard 1.75)")

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 1

    quantized = quantize_to_phrase(
        args.event,
        args.first_kick,
        args.bpm,
        phrase_beats=args.phrase_beats,
        secondary_phrase_beats=args.secondary_phrase_beats,
        tolerance_beats=args.tolerance_beats,
    )
    print(f"{quantized:.3f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
