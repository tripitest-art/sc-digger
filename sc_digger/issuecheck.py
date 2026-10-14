"""Planer-Preflight: prüft einen Issue-Text und seine Akzeptanztests vor dem Einstellen.

Warum es das gibt: Planer ohne Shell legen Worker-Issues mit `entwurf` an, weil sie die
Akzeptanztests nicht ausführen können. Ein kaputter Test fällt dann erst auf, wenn ein Worker
daran scheitert und das Issue eskaliert. Dieses Werkzeug macht die Prüfung zu einem Befehl:
`python -m sc_digger.issuecheck <issue.md>` prüft den Text formal und lässt den Akzeptanztest
gegen ein Repo laufen. Ein roter Test ohne Umsetzung ist das erwünschte Ergebnis.

Nur Standardbibliothek; pytest läuft als Unterprozess. Die Regex für Überschrift und Codeblock
sind aus `.github/scripts/acceptance_guard.py` kopiert, nicht importiert: Das Skript liegt
unter `.github/` und ist für Worker geschützt, ein Import würde die Pfade koppeln.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REQUIRED_HEADINGS = ("Ziel", "Betroffene Dateien", "Schnittstellen", "Nicht Teil dieser Aufgabe",
                     "Akzeptanztests", "Fertig, wenn", "Berührt sc_digger/main.py", "Merge-Modus", "Kontext")

# Importe, die ein Akzeptanztest nicht braucht und die Netzwerk, Uhrzeit oder Zufall ins Spiel
# bringen. `numpy` ist erlaubt, weil Tests damit synthetische Daten bauen.
FORBIDDEN_IMPORTS = frozenset({"requests", "urllib", "socket", "http", "httpx", "ftplib", "smtplib", "random"})
ALLOWED_IMPORT_ROOTS = frozenset({"pytest", "sc_digger", "tests", "numpy"})

# Aufrufe, die den Testlauf von der Uhr oder vom Zufall abhängig machen. Erkannt werden
# Attribut-Zugriffe im Code (`time.sleep(…)`, `datetime.datetime.utcnow()`, `random.random()`),
# nicht bloße Erwähnungen in Zeichenketten: Ein Test, der solche Namen prüft, ist in Ordnung.
_FORBIDDEN_CALLS = ("time.time", "time.sleep")
_FORBIDDEN_CALL_ATTRS = frozenset({"now", "utcnow", "today"})
_SKIP_ATTRS = frozenset({"skip", "skipif", "xfail", "importorskip"})
_HEADING_RE = re.compile(r"^(#{1,6})\s*(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})(.*)$")
_MUSIC_PREFIX = "/music"


@dataclass(frozen=True)
class Finding:
    level: str  # "fehler" | "warnung"
    code: str   # maschinenlesbar
    text: str   # Klartext auf Deutsch


# ---------------- Parsing ----------------
def _headings_outside_fences(lines: list[str]) -> list[tuple[int, int, str]]:
    """(Zeilenindex, Ebene, Text) aller ATX-Überschriften außerhalb von Codeblöcken."""
    in_fence = False
    out: list[tuple[int, int, str]] = []
    for i, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if m:
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return out


def _section_lines(issue_md: str, name: str) -> list[str] | None:
    """Zeilen des Abschnitts `name` (ohne Überschrift) bis zur nächsten Überschrift gleicher oder
    höherer Ebene. None, wenn die Überschrift fehlt."""
    lines = issue_md.replace("\r\n", "\n").split("\n")
    heads = _headings_outside_fences(lines)
    for pos, (line_no, level, text) in enumerate(heads):
        if text.lower() != name.lower():
            continue
        end = len(lines)
        for later_no, later_level, _ in heads[pos + 1:]:
            if later_level <= level:
                end = later_no
                break
        return lines[line_no + 1:end]
    return None


def _iter_blocks(lines: list[str]) -> list[list[str]]:
    """Alle Codeblöcke in `lines` als Inhaltszeilen. Zäune ``` oder ~~~, Länge >= 3."""
    blocks: list[list[str]] = []
    i, n = 0, len(lines)
    while i < n:
        m = _FENCE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        fence = m.group(1)
        close_re = re.compile(r"^" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*$")
        j = i + 1
        body: list[str] = []
        while j < n and not close_re.match(lines[j]):
            body.append(lines[j])
            j += 1
        blocks.append(body)
        break_after = j >= n
        i = j + 1
        if break_after:  # ungeschlossener Zaun: Rest als ein Block werten
            break
    return blocks


def _normalize(code: str) -> str:
    """Zeilenenden normalisieren, Leerraum am Zeilen-/Dateiende entfernen."""
    lines = [ln.rstrip() for ln in code.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip("\n")


def extract_acceptance_block(issue_md: str) -> str | None:
    """Erster Codeblock (``` oder ~~~, beliebig lange Zäune) unter der Überschrift „Akzeptanztests“,
    Zeilenenden normalisiert, Rand-Leerzeilen entfernt. None, wenn es keinen gibt oder er leer ist."""
    section = _section_lines(issue_md, "Akzeptanztests")
    if section is None:
        return None
    blocks = [b for b in _iter_blocks(section) if any(ln.strip() for ln in b)]
    if not blocks:
        return None
    return _normalize("\n".join(blocks[0])) or None


# ---------------- Statische Prüfung ----------------
def _import_roots(tree: ast.AST) -> list[str]:
    """Wurzelmodule aller absoluten Importe, in Reihenfolge, ohne Dubletten."""
    roots: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:  # relative Importe ignorieren
                continue
            roots.append(node.module.split(".")[0])
    seen: list[str] = []
    for root in roots:
        if root not in seen:
            seen.append(root)
    return seen


def _string_literals(tree: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _dotted(node: ast.AST) -> str | None:
    """Gepunkteter Name eines Attribut-/Namens-Zugriffs („time.sleep“), sonst None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _skip_marker(tree: ast.AST) -> str | None:
    """Erster Attribut-Zugriff auf skip/skipif/xfail/importorskip im Code, sonst None.
    Zeichenketten zählen nicht: Wer diese Namen als Daten prüft, überspringt keinen Test."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _SKIP_ATTRS:
            return _dotted(node) or node.attr
    return None


def _forbidden_call(tree: ast.AST) -> str | None:
    """Erster Uhrzeit-/Zufall-Zugriff im Code, sonst None. `time.time`, `time.sleep`,
    jeder `*.now`/`*.utcnow`/`*.today` und alles unter `random.`."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        name = _dotted(node)
        if not name:
            continue
        if name in _FORBIDDEN_CALLS or name.split(".")[-1] in _FORBIDDEN_CALL_ATTRS:
            return name
        if name.split(".")[0] == "random":
            return name
    return None


def check_static(issue_md: str) -> list[Finding]:
    """Prüft den Issue-Text ohne Ausführung (formale Vorgaben und Akzeptanztest-Regeln)."""
    findings: list[Finding] = []
    text = issue_md.replace("\r\n", "\n")
    lines = text.split("\n")

    headings = {name for _, _, name in _headings_outside_fences(lines)}
    for name in REQUIRED_HEADINGS:
        if name not in headings:
            findings.append(Finding("fehler", "ueberschrift-fehlt", f"Pflichtüberschrift fehlt: ### {name}"))

    merge_lines = _section_lines(text, "Merge-Modus")
    if merge_lines is not None:
        merge_low = "\n".join(merge_lines).lower()
        if "automatisch" not in merge_low and "manuell" not in merge_low:
            findings.append(Finding("fehler", "merge-modus",
                                    "Abschnitt „Merge-Modus“ nennt weder „automatisch“ noch „manuell“."))

    section = _section_lines(text, "Akzeptanztests")
    if section is None:  # fehlende Überschrift ist bereits gemeldet
        return _dedupe(findings)

    blocks = [b for b in _iter_blocks(section) if any(ln.strip() for ln in b)]
    if not blocks:
        findings.append(Finding("fehler", "kein-block",
                                "Unter „Akzeptanztests“ steht kein Codeblock (Feld leer oder nicht ausgefüllt)."))
        return _dedupe(findings)
    if len(blocks) > 1:
        findings.append(Finding("fehler", "mehrere-bloecke",
                                f"Unter „Akzeptanztests“ stehen {len(blocks)} Codeblöcke; erwartet wird genau einer."))

    code = _normalize("\n".join(blocks[0]))
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        findings.append(Finding("fehler", "syntaxfehler",
                                f"Der Akzeptanztest ist kein gültiges Python: {exc.msg} (Zeile {exc.lineno})."))
        return _dedupe(findings)

    if not any(isinstance(n, ast.FunctionDef) and n.name.startswith("test_") for n in ast.walk(tree)):
        findings.append(Finding("fehler", "kein-test", "Der Codeblock enthält keine Funktion test_*."))

    for root in _import_roots(tree):
        if root in FORBIDDEN_IMPORTS:
            findings.append(Finding("fehler", "verbotener-import", f"Verbotener Import: {root}"))
        elif root not in ALLOWED_IMPORT_ROOTS and root not in sys.stdlib_module_names:
            findings.append(Finding("warnung", "fremder-import",
                                    f"Import außerhalb von Standardbibliothek/pytest/sc_digger/tests/numpy: {root}"))

    marker = _skip_marker(tree)
    if marker:
        findings.append(Finding("fehler", "skip-xfail", f"Der Akzeptanztest überspringt Tests: {marker}"))

    call = _forbidden_call(tree)
    if call:
        findings.append(Finding("fehler", "verbotener-aufruf",
                                f"Uhrzeit, Zufall oder Warten im Akzeptanztest: {call}"))

    for value in _string_literals(tree):
        if value.startswith(_MUSIC_PREFIX):
            findings.append(Finding("fehler", "musik-pfad",
                                    f"Akzeptanztest fasst die geschützte Sammlung an: {value}"))

    return _dedupe(findings)


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[str, str]] = set()
    out: list[Finding] = []
    for f in findings:
        key = (f.code, f.text)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


# ---------------- Testlauf ----------------
def _count(output: str, word: str) -> int:
    m = re.search(r"(\d+) " + word, output)
    return int(m.group(1)) if m else 0


def check_red(issue_md: str, repo: Path, timeout: float = 300) -> list[Finding]:
    """Führt den Akzeptanztest mit pytest gegen `repo` aus und bewertet das Ergebnis."""
    block = extract_acceptance_block(issue_md)
    if block is None:
        return []
    repo = Path(repo)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo) + os.pathsep + env.get("PYTHONPATH", "")
    # Kein .pyc im fremden Repo: der Testlauf darf den Baum nicht verändern.
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    with tempfile.TemporaryDirectory(prefix="issuecheck-") as tmp:
        testfile = Path(tmp) / "test_issue_preflight.py"
        testfile.write_text(block + "\n", encoding="utf-8")
        cmd = [sys.executable, "-m", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider", str(testfile)]
        try:
            proc = subprocess.run(cmd, cwd=str(repo), env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return [Finding("fehler", "timeout",
                            f"Der Akzeptanztest lief länger als {timeout:g}s; vermutlich wartet er auf Zeit oder Netz.")]
        except OSError as exc:
            return [Finding("fehler", "timeout", f"pytest ließ sich nicht starten: {exc}")]

    output = f"{proc.stdout}\n{proc.stderr}"
    if proc.returncode == 0:
        return [Finding("fehler", "schon-gruen",
                        "Alle Akzeptanztests sind ohne Umsetzung grün; sie prüfen die neue Funktion nicht.")]
    if proc.returncode == 5:
        return [Finding("fehler", "kein-test", "pytest hat im Akzeptanztest keine Testfunktion gesammelt.")]
    if _count(output, "passed"):
        return [Finding("warnung", "teilweise-gruen",
                        "Ein Teil der Akzeptanztests ist ohne Umsetzung schon grün.")]
    return []


def validate(issue_md: str, repo: Path, run_tests: bool = True) -> list[Finding]:
    """check_static; bei Fehlern oder run_tests=False nur das. Sonst zusätzlich check_red."""
    findings = check_static(issue_md)
    if not run_tests or any(f.level == "fehler" for f in findings):
        return findings
    return findings + check_red(issue_md, Path(repo))


# ---------------- Bericht und CLI ----------------
def format_report(findings: list[Finding]) -> str:
    """Eine Zeile je Befund, zuletzt das Urteil für den Planer."""
    lines: list[str] = []
    errors = 0
    for f in findings:
        if f.level == "fehler":
            lines.append(f"❌ [{f.code}] {f.text}")
            errors += 1
        else:
            lines.append(f"⚠️ [{f.code}] {f.text}")
    if errors:
        lines.append(f"Nicht einstellen: {errors} Fehler.")
    else:
        lines.append("Akzeptanztests sind valide und bereit für die Sandbox.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """`issuecheck <datei|-> [--repo DIR] [--no-run]`; „-“ liest stdin. Rückgabe 1 bei Fehlern."""
    parser = argparse.ArgumentParser(prog="python -m sc_digger.issuecheck",
                                     description="Issue-Text und Akzeptanztests vor dem Einstellen prüfen")
    parser.add_argument("datei", help="Issue-Datei oder „-“ für stdin")
    parser.add_argument("--repo", default=".", help="Repo-Wurzel für den Testlauf (Standard: .)")
    parser.add_argument("--no-run", action="store_true", help="Akzeptanztests nicht ausführen")
    args = parser.parse_args(argv)

    if args.datei == "-":
        issue_md = sys.stdin.read()
    else:
        issue_md = Path(args.datei).read_text(encoding="utf-8")

    findings = validate(issue_md, Path(args.repo), run_tests=not args.no_run)
    print(format_report(findings))
    return 1 if any(f.level == "fehler" for f in findings) else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
