"""Abgleich mit der lokalen Sammlung (Logik aus dem soundcloud-schranz-download-Skill)."""
from __future__ import annotations

import logging
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .db import TrackDB
from .models import Config, Track
from .quality import probe as quality_probe

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

# Klammer-Inhalte, die nur Werbung sind ("(EXCLU)", "[Premiere]", "(Out Now)"): kein Remixer.
# Ohne diese Liste zählt z. B. "exclu" als Remixer, und der Treffer scheitert am Dateinamen.
REMIX_NOISE: frozenset[str] = frozenset(
    {"final", "exclu", "exclusive", "premiere", "release", "preview", "snippet", "out", "now"}
)

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
    """Wörter aus Klammern, z. B. '(VRTGØ Edit)' -> {'vrtgo'}. Unterscheidet Edits.

    Klammerinhalte, die nur aus Werbe-Zusätzen bestehen (``REMIX_NOISE``, z. B. "(EXCLU)"
    oder "[Premiere]"), sind kein Remixer und werden übersprungen. Enthält ein Inhalt
    mindestens ein anderes Wort ("(Final Round Edit)"), zählt er wie bisher ganz.
    """
    toks: set[str] = set()
    for inner in PAREN.findall(title):
        words = normalize(inner, fallback=False)
        if not words or all(w in REMIX_NOISE for w in words):
            continue
        toks |= set(words)
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


# Original und Extended Edit sind bei gleichem Namen verschiedene Dateien; getrennte
# Fassungen unterscheiden sich praktisch immer um mehr als ein paar Sekunden.
DURATION_TOLERANCE_S = 5.0


def probe_duration(path: Path) -> float | None:
    """Dauer in Sekunden per ``sc_digger.quality.probe`` (``format.duration``); wirft nie.

    ``None`` bei jedem Fehler (Datei fehlt, kein Audio, ffprobe fehlt) und bei einer Dauer
    <= 0: unbekannt ist kein Ausschlussgrund, der Namens-Treffer bleibt dann gültig.
    """
    try:
        info = quality_probe(Path(path))
        duration = float(info.get("format", {}).get("duration"))
    except Exception:
        return None
    return duration if duration > 0 else None


class Collection:
    """Die lokale Sammlung als Namens-Index; Label eines Eintrags ist der Dateipfad."""

    def __init__(
        self,
        folder: str | Path,
        *,
        duration_fn: Callable[[Path], float | None] | None = probe_duration,
        duration_tolerance_s: float = DURATION_TOLERANCE_S,
        db: TrackDB | None = None,
    ):
        self.folder = Path(folder)
        self.duration_fn = duration_fn
        self.duration_tolerance_s = duration_tolerance_s
        self.db = db
        self.index = NameIndex()
        self.reload()

    def reload(self) -> None:
        self.index = NameIndex()
        self._durations: dict[str, float | None] = {}
        if self.db is not None:
            records = self.db.list_collection_tracks()
            if records:
                for rec in records:
                    self.index.add(rec.path, name_key(None, Path(rec.path).stem))
                    if rec.duration is not None:
                        self._durations[rec.path] = rec.duration
                log.info("Sammlung: %d Dateien (Track-DB)", len(self.index))
                return
        if not self.folder.exists():
            log.warning("Sammlung nicht gefunden: %s (Duplikat-Check übersprungen)", self.folder)
            return
        for p in self.folder.rglob("*"):
            if p.suffix.lower() in AUDIO_EXT:
                self.index.add(str(p), name_key(None, p.stem))
        log.info("Sammlung: %d Dateien", len(self.index))

    def _duration(self, label: str) -> float | None:
        """Dauer der Datei hinter ``label``, je Datei einmal pro Lauf; ``None`` = unbekannt."""
        if label not in self._durations:
            try:
                self._durations[label] = self.duration_fn(Path(label))
            except Exception:
                log.debug("Dauer nicht lesbar: %s", label)
                self._durations[label] = None
        return self._durations[label]

    def find_duplicate(self, t: Track) -> str | None:
        """Gibt den Dateinamen zurück, falls der Track vermutlich schon vorhanden ist.

        Bei mehreren Namens-Treffern entscheidet die Länge: nur wenn ``t.duration_ms``
        bekannt ist und die Dauer der Kandidatendatei um mehr als ``duration_tolerance_s``
        abweicht, wird der Kandidat übersprungen.
        """
        track_seconds = t.duration_ms / 1000 if t.duration_ms > 0 else None
        for label in self.index.find_all(name_key(t.artist, t.title)):
            if track_seconds is not None and self.duration_fn is not None:
                seconds = self._duration(label)
                if seconds is not None and abs(seconds - track_seconds) > self.duration_tolerance_s:
                    log.debug(
                        "Gleicher Name, andere Länge (%.0f s statt %.0f s): %s",
                        seconds, track_seconds, Path(label).name,
                    )
                    continue
            return Path(label).name
        return None

    def mark_duplicates(self, tracks: list[Track]) -> list[Track]:
        for t in tracks:
            t.duplicate_of = self.find_duplicate(t)
        return tracks


def open_collection(cfg: Config) -> Collection:
    """Baut die Sammlung für den täglichen Lauf, aus der Track-DB wenn konfiguriert.

    Ohne ``state.track_db_path`` oder ohne die Datei bleibt es beim Dateisystem-Scan.
    Eine nicht lesbare DB wird nur gewarnt: der Lauf soll ohne Index weitergehen.
    Eine DB wird hier nie angelegt.
    """
    folder = cfg["download"]["collection_dir"]
    db_path = (cfg.raw.get("state") or {}).get("track_db_path")
    if not db_path or not Path(db_path).exists():
        return Collection(folder)
    try:
        db = TrackDB(db_path)
    except sqlite3.Error as e:
        log.warning("Track-DB nicht lesbar (%s), Sammlung wird vom Dateisystem gelesen", e)
        return Collection(folder)
    try:
        return Collection(folder, db=db)
    finally:
        db.close()
