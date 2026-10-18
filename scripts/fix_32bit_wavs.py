#!/usr/bin/env python3
"""Wartungsskript: 32-Bit-/Float-Audio in der Inbox rekursiv nach 24-Bit-PCM wandeln.

Rekordbox und CDJs weisen 32-Bit-WAVs (pcm_f32le, pcm_s32le, …) mit "Ungültiges
Dateiformat" ab. Die Inbox-Pipeline konvertiert neue Downloads automatisch; dieses
Skript räumt Bestände auf, die vorher geladen wurden. Die Sammlung (/music/Schranz)
ist schreibgeschützt und gehört nicht hierher — nur Inbox-Pfade angeben.

Verwendung:
    # Trockenlauf: nur zeigen, was konvertiert würde
    docker compose exec sc-digger python scripts/fix_32bit_wavs.py /music/inbox --dry-run

    # Konvertieren
    docker compose exec sc-digger python scripts/fix_32bit_wavs.py /music/inbox

    # Lokal
    python scripts/fix_32bit_wavs.py /pfad/zur/inbox [--dry-run] [-v]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Projekt-Root zum sys.path hinzufügen, falls das Skript direkt ausgeführt wird
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sc_digger.quality import convert_to_pcm24, is_32bit_or_float

log = logging.getLogger("fix_32bit_wavs")

# Nur unkomprimierte Container: MP3/FLAC/M4A bleiben unangetastet (Float gibt es dort
# nicht in Rekordbox-relevanter Form, und ein Neu-Encode wäre verlustbehaftet).
_SUFFIXES = {".wav", ".aif", ".aiff"}


def fix_directory(root: Path, dry_run: bool = False) -> tuple[int, int, int]:
    """Konvertiert alle 32-Bit-/Float-Dateien unter root. Gibt (geprüft, konvertiert, Fehler)."""
    checked = converted = failed = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _SUFFIXES:
            continue
        checked += 1
        if not is_32bit_or_float(path):
            continue
        if dry_run:
            log.info("würde konvertieren: %s", path)
            converted += 1
            continue
        if convert_to_pcm24(path):
            log.info("konvertiert: %s", path)
            converted += 1
        else:
            log.warning("Konvertierung fehlgeschlagen: %s", path)
            failed += 1
    return checked, converted, failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="32-Bit-/Float-WAV/AIFF rekursiv nach 24-Bit-PCM konvertieren (Rekordbox).",
    )
    parser.add_argument("inbox", type=Path, help="Inbox-Verzeichnis (wird rekursiv durchsucht)")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="nur anzeigen, welche Dateien konvertiert würden (ändert nichts)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug-Logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    root = args.inbox.expanduser()
    if not root.is_dir():
        log.error("Kein Verzeichnis: %s", root)
        return 2

    checked, converted, failed = fix_directory(root, dry_run=args.dry_run)
    prefix = "Trockenlauf: " if args.dry_run else ""
    print(
        f"{prefix}{checked} Audio-Dateien geprüft, {converted} "
        f"{'zu konvertieren' if args.dry_run else 'konvertiert'}, {failed} Fehler"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
