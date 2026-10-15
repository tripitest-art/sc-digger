"""Abgleich mit der lokalen Sammlung (Logik aus dem soundcloud-schranz-download-Skill)."""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .models import Track

log = logging.getLogger(__name__)

AUDIO_EXT = {".wav", ".aiff", ".aif", ".flac", ".mp3", ".m4a"}
STOP = {
    "free", "download", "dl", "downloads", "original", "mix", "edit", "edt",
    "schranz", "rework", "remix", "remixes", "feat", "ft", "the", "and",
    "v1", "v2", "v3", "v4", "v5", "master", "mastered", "extended", "radio",
    "edits", "reworks", "version", "intro", "flip", "bootleg", "tool", "djtool",
    # Artist-Trenner, wie sie in SoundCloud-Titeln stehen ("A x B", "A vs B", "A with B")
    "x", "vs", "with", "presents", "pres",
}
# Klammer-Inhalt enthält oft den Remixer -> separat behalten
PAREN = re.compile(r"[\(\[]([^\)\]]+)[\)\]]")

# Sonderzeichen, die NFKD nicht in ASCII zerlegt (ø, ł, đ, æ, œ) oder die als Lautfolge
# gelesen werden müssen (ä -> ae statt a). Reihenfolge: NFC, diese Tabelle, dann NFKD.
_FOLD = {
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "ø": "o",
    "ł": "l", "đ": "d", "æ": "ae", "œ": "oe",
}


def _fold_unicode(s: str) -> str:
    """Faltet Akzente und Sonderzeichen nach ASCII: NFC -> _FOLD -> NFKD -> Akzente weg."""
    s = unicodedata.normalize("NFC", s).lower()
    s = "".join(_FOLD.get(ch, ch) for ch in s)
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def normalize(s: str, *, fallback: bool = True) -> list[str]:
    """Zerlegt einen Namen in Vergleichswörter (klein, nur a-z0-9, ohne Stoppwörter).

    ``fallback`` fängt Namen auf, die nur aus Stoppwörtern bestehen: bei "Original Mix"
    bliebe sonst nichts zu vergleichen übrig, deshalb kommen dann die ungefilterten Wörter
    zurück. ``remixer_tokens`` ruft mit ``fallback=False`` auf, damit "(Original Mix)" nicht
    selbst zum Remixer-Namen wird.
    """
    s = _fold_unicode(s).replace("&", " and ")
    s = re.sub(r"['’`]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    words = s.split()
    kept = [w for w in words if w not in STOP]
    return kept if kept or not fallback else words


def remixer_tokens(title: str) -> set[str]:
    """Wörter aus Klammern, z. B. '(VRTGØ Edit)' -> {'vrtgo'}. Unterscheidet Edits."""
    toks: set[str] = set()
    for inner in PAREN.findall(title):
        toks |= set(normalize(inner, fallback=False))
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


@dataclass(frozen=True)
class NameKey:
    """Vergleichsform eines Namens: Wörter und die getrennt gehaltenen Remixer-Tokens."""

    tokens: frozenset[str]
    remix: frozenset[str]


def name_key(artist: str | None, title: str) -> NameKey:
    """Baut den Schlüssel aus Artist und Titel; ``artist=None``/leer zählt wie ``""``."""
    full = f"{artist or ''} {title or ''}".strip()
    return NameKey(
        tokens=frozenset(normalize(full)),
        remix=frozenset(remixer_tokens(title or "")),
    )


def names_match(a: NameKey, b: NameKey) -> bool:
    """Prüft zwei Schlüssel symmetrisch: Wort-Ähnlichkeit plus Remixer-Regeln.

    Das Original („Titel“) und ein Edit („Titel (X Edit)“) sind verschiedene Fassungen:
    wer das Original sucht, darf an einem fremden Edit nicht hängen bleiben – und wer den
    Edit sucht, findet ihn nicht hinter dem Original.
    """
    if not is_match(set(a.tokens), set(b.tokens)):
        return False
    if a.remix and b.remix and not (a.remix & b.remix):
        return False
    if a.remix and not b.remix and not (a.remix & b.tokens):
        return False
    if b.remix and not a.remix and not (b.remix & a.tokens):
        return False
    return True


class NameIndex:
    """Sucht Namens-Schlüssel in Einfüge-Reihenfolge über ein Wort-Verzeichnis.

    Verglichen werden nur Einträge, die mindestens ein Wort mit der Suche teilen: ohne
    gemeinsames Wort ist ``is_match`` ohnehin falsch, so bleibt der Aufwand unabhängig
    von der Größe der Sammlung.
    """

    def __init__(self) -> None:
        self._keys: list[NameKey] = []
        self._labels: list[str] = []
        self._by_word: dict[str, list[int]] = {}

    def __len__(self) -> int:
        return len(self._keys)

    def add(self, label: str, key: NameKey) -> None:
        idx = len(self._keys)
        self._keys.append(key)
        self._labels.append(label)
        for word in key.tokens:
            self._by_word.setdefault(word, []).append(idx)

    def find_all(self, key: NameKey) -> list[str]:
        """Labels aller passenden Einträge in Einfüge-Reihenfolge; leerer Schlüssel -> []."""
        if not key.tokens:
            return []
        candidates: set[int] = set()
        for word in key.tokens:
            candidates.update(self._by_word.get(word, ()))
        return [
            self._labels[i]
            for i in sorted(candidates)
            if names_match(key, self._keys[i])
        ]

    def find(self, key: NameKey) -> str | None:
        """Erstes Label aus :meth:`find_all` oder ``None``."""
        found = self.find_all(key)
        return found[0] if found else None


class Collection:
    """Die lokale Sammlung als Namens-Index; Label eines Eintrags ist der Dateipfad."""

    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self.index = NameIndex()
        self.reload()

    def reload(self) -> None:
        self.index = NameIndex()
        if not self.folder.exists():
            log.warning("Sammlung nicht gefunden: %s (Duplikat-Check übersprungen)", self.folder)
            return
        for p in self.folder.rglob("*"):
            if p.suffix.lower() in AUDIO_EXT:
                self.index.add(str(p), name_key(None, p.stem))
        log.info("Sammlung: %d Dateien", len(self.index))

    def find_duplicate(self, t: Track) -> str | None:
        """Gibt den Dateinamen zurück, falls der Track vermutlich schon vorhanden ist."""
        label = self.index.find(name_key(t.artist, t.title))
        return Path(label).name if label else None

    def mark_duplicates(self, tracks: list[Track]) -> list[Track]:
        for t in tracks:
            t.duplicate_of = self.find_duplicate(t)
        return tracks
