"""Changelog aus Fragmenten (Issue #536).

Jeder PR legt eine Datei ``changelog.d/<PR-Nummer>.md`` an, statt in ``CHANGELOG.md`` zu
schreiben. Alle PRs änderten dieselbe Stelle unter „Unreleased“, das erzeugte bei jedem
parallelen Merge einen Konflikt. Ein Sammel-PR (Label ``changelog-zusammenfuehren``) führt die
Fragmente mit ``merge`` in ``CHANGELOG.md`` zusammen und löscht sie.

CLI::

    python scripts/changelog.py merge [--changelog CHANGELOG.md] [--dir changelog.d]
    python scripts/changelog.py check --labels "a,b" < geaenderte_dateien.txt
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

# Feste Reihenfolge der Abschnitte unter „Unreleased“.
SECTIONS = ["Hinzugefügt", "Geändert", "Behoben", "Entfernt"]
NO_CHANGELOG_LABEL = "kein-changelog"
# Sammel-PR, der die Fragmente in CHANGELOG.md zusammenführt (main-schutz verbietet Direkt-Pushes).
ASSEMBLE_LABEL = "changelog-zusammenfuehren"
FRAGMENT_DIR = "changelog.d"

_PR_REF = re.compile(r"\(#\d+\)")
_HEADING = re.compile(r"^###\s+(.+?)\s*$")


def _parse_fragment(path: pathlib.Path) -> dict[str, list[str]]:
    """Liest ein Fragment als {Abschnitt: [Einträge]}; ergänzt fehlende (#Nr) aus dem Dateinamen."""
    nummer = path.stem if path.stem.isdigit() else None
    result: dict[str, list[str]] = {}
    section: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _HEADING.match(line)
        if m:
            section = m.group(1)
            if section not in SECTIONS:
                raise ValueError(f"{path.name}: unbekannter Abschnitt „{section}“ (erlaubt: {', '.join(SECTIONS)})")
            result.setdefault(section, [])
        elif line.startswith("- "):
            if section is None:
                raise ValueError(f"{path.name}: Eintrag vor der ersten Überschrift „### …“")
            result[section].append(line.rstrip())
        elif line.strip() and result.get(section):
            # Fortsetzungszeile des letzten Eintrags
            result[section][-1] += "\n" + line.rstrip()
        elif line.strip():
            raise ValueError(f"{path.name}: Text außerhalb eines Eintrags: {line!r}")
    for entries in result.values():
        for i, entry in enumerate(entries):
            if nummer and not _PR_REF.search(entry):
                entries[i] = f"{entry} (#{nummer})"
    return result


def _unreleased_bounds(lines: list[str]) -> tuple[int, int]:
    start = next((i for i, l in enumerate(lines) if l.strip() == "## Unreleased"), None)
    if start is None:
        raise ValueError("Abschnitt „## Unreleased“ fehlt in CHANGELOG.md")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start, end


def merge_fragments(changelog_path, fragment_dir) -> list[pathlib.Path]:
    """Führt alle Fragmente unter „Unreleased“ zusammen und löscht sie.

    Gibt die gelöschten Dateien zurück. Ohne Fragmente bleibt ``CHANGELOG.md`` byte-gleich.
    Neueste Einträge (höchste Nummer) stehen oben im Abschnitt. Bei einem Fehler in einem
    Fragment wird nichts geschrieben und nichts gelöscht.
    """
    changelog_path = pathlib.Path(changelog_path)
    fragment_dir = pathlib.Path(fragment_dir)
    files = sorted(
        (p for p in fragment_dir.glob("*.md") if p.is_file()),
        key=lambda p: (int(p.stem) if p.stem.isdigit() else -1, p.name),
    )
    if not files:
        return []

    neu: dict[str, list[str]] = {}
    for path in files:  # aufsteigend, damit die höchste Nummer zuletzt (= ganz oben) landet
        for section, entries in _parse_fragment(path).items():
            neu.setdefault(section, [])
            for entry in entries:
                neu[section].insert(0, entry)

    # Zeilenenden erhalten: newline="" verhindert, dass Python \r\n umschreibt.
    with open(changelog_path, encoding="utf-8", newline="") as fh:
        text = fh.read()
    lines = text.split("\n")
    for section in SECTIONS:
        if section not in neu:
            continue
        start, end = _unreleased_bounds(lines)
        heading = f"### {section}"
        idx = next((i for i in range(start + 1, end) if lines[i].strip() == heading), None)
        if idx is not None:
            lines[idx + 1 : idx + 1] = neu[section]
            continue
        # Abschnitt fehlt: vor dem ersten vorhandenen Abschnitt mit höherem Rang einfügen,
        # sonst ans Ende von „Unreleased“.
        rang = SECTIONS.index(section)
        ziel = end
        for i in range(start + 1, end):
            m = _HEADING.match(lines[i])
            if m and m.group(1) in SECTIONS and SECTIONS.index(m.group(1)) > rang:
                ziel = i
                break
        block = [heading, *neu[section], ""]
        if ziel == end:
            # hinter den letzten Inhalt, vor trailing Leerzeilen/nächsten Abschnitt
            while ziel > start + 1 and not lines[ziel - 1].strip():
                ziel -= 1
            block = ["", heading, *neu[section]]
            if end < len(lines) and lines[end].startswith("## "):
                block.append("")
        lines[ziel:ziel] = block

    neuer_text = "\n".join(lines)
    if not neuer_text.endswith("\n"):
        neuer_text += "\n"
    with open(changelog_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(neuer_text)
    for path in files:
        path.unlink()
    return files


def check_pr(changed_paths, labels) -> list[str]:
    """Prüft einen PR: Fragment unter ``changelog.d/`` (oder Label ``kein-changelog``).

    ``CHANGELOG.md`` selbst darf kein PR ändern, sonst kehren die Konflikte zurück;
    Ausnahme ist der Sammel-PR mit Label ``changelog-zusammenfuehren``.
    """
    paths = list(changed_paths)
    if ASSEMBLE_LABEL in set(labels):
        return []  # Sammel-PR: ändert CHANGELOG.md und löscht Fragmente, braucht keins
    fehler: list[str] = []
    if "CHANGELOG.md" in paths:
        fehler.append(
            "CHANGELOG.md darf im PR nicht geändert werden: Eintrag als Fragment in "
            f"{FRAGMENT_DIR}/<PR-Nummer>.md schreiben."
        )
    hat_fragment = any(
        p.startswith(FRAGMENT_DIR + "/") and p.endswith(".md") for p in paths
    )
    if not hat_fragment and NO_CHANGELOG_LABEL not in set(labels):
        fehler.append(
            f"Changelog-Fragment fehlt: {FRAGMENT_DIR}/<PR-Nummer>.md anlegen "
            f"(oder Label „{NO_CHANGELOG_LABEL}“ setzen, wenn der PR nichts für den Deploy ändert)."
        )
    return fehler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge", help="Fragmente in CHANGELOG.md zusammenführen")
    m.add_argument("--changelog", default="CHANGELOG.md")
    m.add_argument("--dir", default=FRAGMENT_DIR)
    c = sub.add_parser("check", help="PR prüfen: geänderte Dateien von stdin, Labels per --labels")
    c.add_argument("--labels", default="", help="kommagetrennte Label-Namen")
    args = ap.parse_args(argv)

    if args.cmd == "merge":
        try:
            geloescht = merge_fragments(args.changelog, args.dir)
        except ValueError as e:
            print(f"Fehler: {e}", file=sys.stderr)
            return 1
        print(f"{len(geloescht)} Fragment(e) zusammengeführt")
        return 0

    paths = [l.strip() for l in sys.stdin.read().splitlines() if l.strip()]
    labels = [l.strip() for l in args.labels.split(",") if l.strip()]
    fehler = check_pr(paths, labels)
    for f in fehler:
        print(f"::error::{f}")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
