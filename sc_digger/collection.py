"""Abgleich mit der lokalen Sammlung (Logik aus dem soundcloud-schranz-download-Skill)."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from .models import Track

log = logging.getLogger(__name__)

AUDIO_EXT = {".wav", ".aiff", ".aif", ".flac", ".mp3", ".m4a"}
STOP = {
    "free", "download", "dl", "downloads", "original", "mix", "edit", "edt",
    "schranz", "rework", "remix", "remixes", "feat", "ft", "the", "and",
    "v1", "v2", "v3", "v4", "v5", "master", "mastered", "extended", "radio",
    "edits", "reworks", "version", "intro", "flip", "bootleg", "tool", "djtool",
}
# Klammer-Inhalt enthält oft den Remixer -> separat behalten
PAREN = re.compile(r"[\(\[]([^\)\]]+)[\)\]]")


def normalize(s: str) -> list[str]:
    s = s.lower().replace("&", " and ")
    s = re.sub(r"['’`]", "", s)
    s = re.sub(r"[^a-z0-9äöüßø ]", " ", s)
    return [w for w in s.split() if w and w not in STOP]


def remixer_tokens(title: str) -> set[str]:
    """Wörter aus Klammern, z. B. '(VRTGØ Edit)' -> {'vrtgø'}. Unterscheidet Edits."""
    toks: set[str] = set()
    for inner in PAREN.findall(title):
        toks |= set(normalize(inner))
    return toks


def _score(a: set[str], b: set[str]) -> tuple[float, float, int]:
    if not a or not b:
        return 0.0, 0.0, 0
    inter = len(a & b)
    return inter / max(len(a), len(b)), inter / min(len(a), len(b)), inter


def is_match(a: set[str], b: set[str]) -> bool:
    jac, min_ratio, n = _score(a, b)
    return (
        (jac >= 0.45 and n >= 2)
        or (min_ratio >= 0.8 and n >= 2)
        or (n >= 1 and jac >= 0.75)
    )


class Collection:
    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self.entries: list[tuple[str, set[str], set[str]]] = []
        self.reload()

    def reload(self) -> None:
        self.entries.clear()
        if not self.folder.exists():
            log.warning("Sammlung nicht gefunden: %s (Duplikat-Check übersprungen)", self.folder)
            return
        for p in self.folder.rglob("*"):
            if p.suffix.lower() in AUDIO_EXT:
                self.entries.append(
                    (p.name, set(normalize(p.stem)), remixer_tokens(p.stem))
                )
        log.info("Sammlung: %d Dateien", len(self.entries))

    def find_duplicate(self, t: Track) -> str | None:
        """Gibt den Dateinamen zurück, falls der Track vermutlich schon vorhanden ist."""
        full = f"{t.artist} {t.title}"
        a = set(normalize(full))
        a_remix = remixer_tokens(t.title)
        for name, b, b_remix in self.entries:
            if not is_match(a, b):
                continue
            # Regel aus dem Skill: bei Edits/Reworks muss der Remixer-Name passen,
            # sonst gilt ein anderer Edit desselben Originals fälschlich als vorhanden.
            if a_remix and b_remix and not (a_remix & b_remix):
                continue
            if a_remix and not b_remix and not (a_remix & b):
                continue
            return name
        return None

    def mark_duplicates(self, tracks: list[Track]) -> list[Track]:
        for t in tracks:
            t.duplicate_of = self.find_duplicate(t)
        return tracks
