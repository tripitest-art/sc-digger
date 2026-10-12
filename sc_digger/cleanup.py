"""Inbox-Cleanup: Dubletten derselben SoundCloud-ID finden, auflisten und optional verschieben.

Grundlage ist der Dateiname ([<id>]). Es wird nie gelöscht; überzählige Dateien landen
optional in <inbox>/_rejected/duplicate/, bei Namenskollision durchnummeriert.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from sc_digger.rekordbox import (
    InboxTrack,
    _dedupe_sort_key,
    scan_inbox,
    soundcloud_id_from_name,
)


@dataclass
class DuplicateGroup:
    soundcloud_id: str
    keep: InboxTrack
    duplicates: list[InboxTrack]


def find_inbox_duplicates(tracks: list[InboxTrack]) -> list[DuplicateGroup]:
    """Gruppiert Tracks nach SoundCloud-ID (aus soundcloud_id_from_name(path.name)).
    Nur IDs mit mindestens 2 Tracks bilden eine DuplicateGroup.
    Pro ID bestimmt _dedupe_sort_key(t) die beste Datei (keep, Index 0).
    Alle weiteren Dateien werden als duplicates (Index 1..) abgelegt, ebenfalls sortiert.
    Rückgabe: Liste von DuplicateGroup, aufsteigend nach soundcloud_id sortiert.
    Tracks ohne ID oder ohne Dubletten werden ignoriert. Keine Datei-I/O."""
    buckets: dict[str, list[InboxTrack]] = {}
    for t in tracks:
        sc_id = soundcloud_id_from_name(t.path.name)
        if sc_id is None:
            continue
        buckets.setdefault(sc_id, []).append(t)

    groups: list[DuplicateGroup] = []
    for sc_id in sorted(buckets):
        members = buckets[sc_id]
        if len(members) < 2:
            continue
        members = sorted(members, key=_dedupe_sort_key)
        groups.append(DuplicateGroup(soundcloud_id=sc_id, keep=members[0], duplicates=members[1:]))
    return groups


def audit_inbox_duplicates(inbox: Path) -> list[DuplicateGroup]:
    """Liest Tracks via scan_inbox(inbox) und gibt find_inbox_duplicates(tracks) zurück.
    Existiert inbox nicht -> []."""
    inbox = Path(inbox)
    if not inbox.exists():
        return []
    return find_inbox_duplicates(scan_inbox(inbox))


def _rel_path(t: InboxTrack, inbox: Path | None) -> str:
    if inbox is None:
        return t.path.as_posix()
    try:
        return t.path.relative_to(inbox).as_posix()
    except ValueError:
        return t.path.as_posix()


def _bpm_str(t: InboxTrack) -> str:
    if t.bpm is None:
        return "? BPM"
    return f"{t.bpm:.1f} BPM"


def _key_str(t: InboxTrack) -> str:
    return t.key.strip() if t.key.strip() else "?"


def _size_mb(size: int) -> float:
    return size / (1024 * 1024)


def format_duplicate_report(groups: list[DuplicateGroup], inbox: Path | None = None) -> str:
    """Erzeugt formatierten Textbericht über gefundene Dubletten.
    Format pro Gruppe:
      SoundCloud-ID <id> (<titel>):
        [BEHALTEN]   <rel_path> | <bpm> BPM | <key> | <size> MB
        [ÜBERZÄHLIG] <rel_path> | <bpm> BPM | <key> | <size> MB
    Abschlusszeile: Zusammenfassung mit Anzahl Gruppen, überzähligen Dateien und freigebbarer Größe in MB.
    Keine Dubletten (groups leer) -> "Keine Dubletten in der Inbox gefunden."."""
    if not groups:
        return "Keine Dubletten in der Inbox gefunden."

    lines: list[str] = []
    total_dupes = 0
    freeable_bytes = 0

    for g in groups:
        lines.append(f"SoundCloud-ID {g.soundcloud_id} ({g.keep.title}):")
        lines.append(
            f"  [BEHALTEN]   {_rel_path(g.keep, inbox)} | {_bpm_str(g.keep)} | "
            f"{_key_str(g.keep)} | {_size_mb(g.keep.size):.1f} MB"
        )
        for d in g.duplicates:
            lines.append(
                f"  [ÜBERZÄHLIG] {_rel_path(d, inbox)} | {_bpm_str(d)} | "
                f"{_key_str(d)} | {_size_mb(d.size):.1f} MB"
            )
            total_dupes += 1
            freeable_bytes += d.size

    lines.append("")
    lines.append(
        f"Zusammenfassung: {len(groups)} Gruppe(n), {total_dupes} überzählige Datei(en), "
        f"{_size_mb(freeable_bytes):.1f} MB freigebbar."
    )
    return "\n".join(lines)


def _free_target(dest: Path) -> Path:
    """Weicht bei Namenskollision auf <stem>_1<ext>, <stem>_2<ext>, ... aus. Löscht nie."""
    if not dest.exists():
        return dest
    stem = dest.stem
    ext = dest.suffix
    counter = 1
    while True:
        candidate = dest.with_name(f"{stem}_{counter}{ext}")
        if not candidate.exists():
            return candidate
        counter += 1


def move_duplicates(groups: list[DuplicateGroup], inbox: Path) -> list[tuple[Path, Path]]:
    """Verschiebt überzählige Dateien (t in group.duplicates) nach inbox / "_rejected" / "duplicate".
    Die behaltene Datei (group.keep) wird niemals bewegt.
    Niemals löschen: existiert die Zieldatei bereits, wird durchnummeriert (<stem>_1<ext>, <stem>_2<ext>, ...).
    Rückgabe: Liste von Tupeln (ursprünglicher_pfad, zielpfad) aller tatsächlich verschobenen Dateien."""
    inbox = Path(inbox)
    target_dir = inbox / "_rejected" / "duplicate"
    moved: list[tuple[Path, Path]] = []

    for g in groups:
        for d in g.duplicates:
            src = Path(d.path)
            if not src.exists():
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            dest = _free_target(target_dir / src.name)
            shutil.move(str(src), str(dest))
            moved.append((src, dest))
    return moved
