"""acceptance-guard: prüft einen Pull Request, ohne dessen Code auszuführen.

Warum es das gibt: Ein Worker-Agent, der Tests „grün machen“ soll, kann sie auch grün
machen, indem er sie abschwächt. Dieser Check ist der unabhängige Schiedsrichter:

1. Bereits gemergte Akzeptanztests (tests/acceptance/) sind unveränderlich.
2. Verlinkt der PR ein Issue („Closes #N“) mit Akzeptanztest-Block, muss
   tests/acceptance/test_issue_N.py genau diesem Block entsprechen.
3. Keine neuen skip/xfail-Markierungen in Tests.
4. CI- und Test-Infrastruktur (.github/, conftest.py, pytest-Konfiguration) bleibt unberührt.
5. Hat das verlinkte Issue einen Abschnitt „Betroffene Dateien“, ändert der PR nur diese
   Dateien (plus Dateien unter tests/). Sonst stimmt entweder der PR nicht oder das Issue.

Verstöße gegen 1, 3, 4 und 5 kann Stephan bewusst mit dem Label `freigabe-geschützt` erlauben.
Regel 2 kennt keine Ausnahme: Stimmt der Test nicht, wird das Issue korrigiert.

Läuft als pull_request_target mit dem Stand aus `main` und liest den PR nur über die
GitHub-API. Nur Standardbibliothek, damit der Job ohne pip auskommt.
"""

from __future__ import annotations

import difflib
import fnmatch
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Callable

OVERRIDE_LABEL = "freigabe-geschützt"
ACCEPTANCE_DIR = "tests/acceptance/"

# Dateien, über die sich Tests oder CI still aushebeln lassen.
PROTECTED_NAMES = {"conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"}
PROTECTED_PREFIXES = (".github/",)

_LINK_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b", re.I)
_HEADING_RE = re.compile(r"^(#{1,6})\s*(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})[^\n]*\n(.*?)\n?^\1[ \t]*$", re.M | re.S)
# Pfad in „Betroffene Dateien“: `sc_digger/db.py`, `config.yaml`, `docs/`,
# `tests/acceptance/test_issue_<N>.py`. Kein führender Slash (API-Endpunkte wie `/tracks/{id}`),
# keine Klammern oder Leerzeichen (Signaturen wie `get_likers(track_id)`).
_PATH_RE = re.compile(r"^(?!/)[\w.*<>-]+(?:/[\w.*<>-]*)*$")
_PLACEHOLDER_RE = re.compile(r"<[^<>/]*>")
_BULLET_RE = re.compile(r"^ {0,3}[-*+]\s+(?:\[[ xX]\]\s+)?(.*)$")
_TICKED_RE = re.compile(r"`([^`\n]+)`")
# Tests darf der Worker immer ergänzen (Issue-Formular: „plus die Testdateien“).
# conftest.py und bestehende Akzeptanztests fangen die Regeln 1 und 4 ab.
ALWAYS_ALLOWED_PREFIXES = ("tests/",)
# Doku-Nachträge (PR-Vorlage: „Doku angepasst“) blockieren nicht, erscheinen aber als Hinweis
# fürs Review. AGENTS.md gehört bewusst nicht dazu: Regeln ändert nur ein eigener PR.
DOC_FILES = {"README.md", "ROADMAP.md"}
_SKIP_RE = re.compile(
    r"pytest\.mark\.(?:skip|skipif|xfail)\b|pytest\.(?:skip|xfail|importorskip)\s*\("
)


@dataclass
class Result:
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


# ---------------- Parsing ----------------
def linked_issues(pr_body: str | None) -> list[int]:
    """Issue-Nummern aus „Closes #12“, „fixes #3“, „Resolves: #7“ (Reihenfolge erhalten)."""
    seen: list[int] = []
    for m in _LINK_RE.finditer(pr_body or ""):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen


def _section(issue_body: str | None, title: str) -> str | None:
    """Text unter der ersten Überschrift, die mit `title` beginnt, bis zur nächsten gleich
    hohen oder höheren Überschrift; None, wenn es die Überschrift nicht gibt.

    Issue-Formulare rendern Felder als „### <Label>“, handgeschriebene Issues dürfen jede
    Überschriften-Ebene nutzen. Überschriften in Code-Blöcken beenden den Abschnitt nicht.
    """
    lines = (issue_body or "").replace("\r\n", "\n").split("\n")
    start = level = None
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m and m.group(2).lower().startswith(title):
            start, level = i + 1, len(m.group(1))
            break
    if start is None:
        return None
    end = len(lines)
    in_fence = False
    for i in range(start, len(lines)):
        if re.match(r"^(`{3,}|~{3,})", lines[i]):
            in_fence = not in_fence
        m = None if in_fence else _HEADING_RE.match(lines[i])
        if m and len(m.group(1)) <= level:
            end = i
            break
    return "\n".join(lines[start:end])


def extract_acceptance_block(issue_body: str | None) -> str | None:
    """Code-Block unter der Überschrift „Akzeptanztests“ oder None.

    Ein leeres Formularfeld rendert GitHub als „_No response_“, also ohne Code-Block.
    """
    fence = _FENCE_RE.search(_section(issue_body, "akzeptanztests") or "")
    if not fence:
        return None
    code = normalize(fence.group(2))
    return code or None


def extract_allowed_files(issue_body: str | None, issue_number: int) -> list[str] | None:
    """Pfade aus dem Abschnitt „Betroffene Dateien“ oder None, wenn es ihn nicht gibt.

    Gezählt werden nur Listenpunkte, keine Fortsetzungszeilen. Pro Punkt gilt der erste
    Pfad (mit oder ohne Backticks), dazu weitere Pfade in Backticks mit Schrägstrich
    („`tests/test_a.py` oder neue `tests/test_b.py`“). Andere Backtick-Wörter sind
    Erläuterung, keine Datei (`config.yaml`: neues Feld `digest.max_items`).
    Platzhalter wie <N> werden durch die Issue-Nummer ersetzt.
    """
    section = _section(issue_body, "betroffene dateien")
    if section is None:
        return None
    found: list[str] = []
    in_fence = False
    for line in section.split("\n"):
        if re.match(r"^\s*(`{3,}|~{3,})", line):
            in_fence = not in_fence
            continue
        m = None if in_fence else _BULLET_RE.match(line)
        if not m:
            continue
        item = m.group(1).strip()
        ticked = _TICKED_RE.findall(item)
        if item.startswith("`"):
            candidates = ticked[:1] + [t for t in ticked[1:] if "/" in t]
        else:
            words = item.split()
            candidates = ([words[0].rstrip(":,;")] if words else []) + [t for t in ticked if "/" in t]
        for c in candidates:
            c = c.strip()
            if _PATH_RE.match(c) and ("." in c or "/" in c):
                path = _PLACEHOLDER_RE.sub(str(issue_number), c)
                if path not in found:
                    found.append(path)
    return found


def path_allowed(path: str, allowed: list[str]) -> bool:
    """Exakter Pfad, Ordner („docs/“) oder Muster („sc_digger/*.py“) aus dem Issue."""
    if path.startswith(ALWAYS_ALLOWED_PREFIXES):
        return True
    return any(
        path == a or (a.endswith("/") and path.startswith(a)) or fnmatch.fnmatchcase(path, a)
        for a in allowed
    )


def normalize(code: str) -> str:
    """Zeilenenden und Leerraum am Zeilen-/Dateiende sind keine inhaltliche Änderung."""
    lines = [ln.rstrip() for ln in code.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip("\n")


def added_skip_markers(patch: str | None) -> list[str]:
    """Hinzugefügte Diff-Zeilen, die Tests überspringen oder als erwarteten Fehler markieren."""
    hits = []
    for line in (patch or "").split("\n"):
        if line.startswith("+") and not line.startswith("+++") and _SKIP_RE.search(line):
            hits.append(line[1:].strip())
    return hits


def is_protected_infra(path: str) -> bool:
    return path.startswith(PROTECTED_PREFIXES) or PurePosixPath(path).name in PROTECTED_NAMES


# ---------------- Bewertung ----------------
def evaluate(
    files: list[dict],
    labels: set[str],
    issues: dict[int, str | None],
    read_head_file: Callable[[str], str | None],
) -> Result:
    """Reine Logik, damit sie ohne GitHub testbar ist.

    files: Einträge aus GET /pulls/{n}/files (filename, status, previous_filename, patch)
    issues: verlinkte Issues -> Body (nur echte Issues, keine PRs)
    read_head_file: liest eine Datei im Stand des PR-Heads, None wenn sie fehlt
    """
    res = Result()
    override = OVERRIDE_LABEL in labels

    def protected(msg: str) -> None:
        if override:
            res.notes.append(f"Erlaubt durch Label `{OVERRIDE_LABEL}`: {msg}")
        else:
            res.violations.append(msg)

    for f in files:
        name, status = f["filename"], f.get("status", "modified")
        old = f.get("previous_filename")

        # 1. Gemergte Akzeptanztests sind unveränderlich; neue dürfen dazukommen.
        touched_existing = [p for p in (name, old) if p and p.startswith(ACCEPTANCE_DIR)]
        if touched_existing and status != "added":
            protected(f"Bestehender Akzeptanztest geändert ({status}): `{old or name}`")

        # 3. Kein neues Überspringen von Tests.
        if name.startswith("tests/") or PurePosixPath(name).name.startswith("test_"):
            if f.get("patch") is None and status not in ("removed",):
                res.notes.append(f"Kein Diff von GitHub für `{name}` (zu groß?), skip-Prüfung nicht möglich.")
            for hit in added_skip_markers(f.get("patch")):
                protected(f"Neues skip/xfail in `{name}`: `{hit}`")

        # 4. CI und Test-Konfiguration.
        for p in {name, old} - {None}:
            if is_protected_infra(p):
                protected(f"CI-/Test-Infrastruktur geändert: `{p}`")

    # 5. Nur Dateien aus „Betroffene Dateien“ (bei mehreren verlinkten Issues: Vereinigung).
    allowed: list[str] = []
    listed_in: list[int] = []
    for number, body in issues.items():
        paths = extract_allowed_files(body, number)
        if paths is None:
            res.notes.append(f"Issue #{number} hat keinen Abschnitt „Betroffene Dateien“; Umfang ungeprüft.")
            continue
        listed_in.append(number)
        allowed += [p for p in paths if p not in allowed]
    if listed_in:
        refs = ", ".join(f"#{n}" for n in listed_in)
        touched = {p for f in files for p in (f["filename"], f.get("previous_filename")) if p}
        outside = sorted(p for p in touched if not path_allowed(p, allowed))
        for p in [p for p in outside if p in DOC_FILES]:
            res.notes.append(f"Doku `{p}` geändert, steht nicht im Issue: im Review prüfen.")
        outside = [p for p in outside if p not in DOC_FILES]
        for p in outside:
            protected(f"`{p}` steht nicht unter „Betroffene Dateien“ in Issue {refs}. "
                      "Änderung zurücknehmen oder das Issue ergänzen lassen.")
        if outside:
            listed = ", ".join(f"`{a}`" for a in allowed) or "keine"
            res.notes.append(f"Laut Issue erlaubt: {listed}, dazu alles unter `tests/`.")
        else:
            res.notes.append(f"Geänderte Dateien passen zu „Betroffene Dateien“ in Issue {refs}.")

    # 2. Akzeptanztest muss exakt dem Issue entsprechen (keine Ausnahme per Label).
    for number, body in issues.items():
        expected = extract_acceptance_block(body)
        path = f"{ACCEPTANCE_DIR}test_issue_{number}.py"
        if expected is None:
            res.notes.append(f"Issue #{number} hat keinen Akzeptanztest-Block.")
            continue
        actual = read_head_file(path)
        if actual is None:
            res.violations.append(f"`{path}` fehlt. Akzeptanztests aus Issue #{number} 1:1 übernehmen.")
            continue
        if normalize(actual) != expected:
            diff = "\n".join(
                difflib.unified_diff(
                    expected.split("\n"), normalize(actual).split("\n"),
                    f"Issue #{number}", path, lineterm="", n=1,
                )
            )
            short = "\n".join(diff.split("\n")[:40])
            res.violations.append(
                f"`{path}` weicht vom Akzeptanztest in Issue #{number} ab. "
                f"Ist der Test falsch, das Issue korrigieren, nicht die Datei.\n```diff\n{short}\n```"
            )
        else:
            res.notes.append(f"`{path}` entspricht Issue #{number}.")

    return res


# ---------------- GitHub-Anbindung ----------------
class GitHub:
    def __init__(self, repo: str, token: str, api: str = "https://api.github.com"):
        self.base = f"{api}/repos/{repo}"
        self.token = token

    def _get(self, path: str, raw: bool = False):
        req = urllib.request.Request(
            self.base + path,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        return data.decode("utf-8", "replace") if raw else json.loads(data)

    def pr_files(self, number: int) -> list[dict]:
        files, page = [], 1
        while True:
            batch = self._get(f"/pulls/{number}/files?per_page=100&page={page}") or []
            files += batch
            if len(batch) < 100:
                return files
            page += 1

    def issue_body(self, number: int) -> tuple[bool, str | None]:
        """(ist_echtes_issue, body)"""
        d = self._get(f"/issues/{number}")
        if not d or "pull_request" in d:
            return False, None
        return True, d.get("body")

    def file_at(self, path: str, ref: str) -> str | None:
        return self._get(f"/contents/{path}?ref={ref}", raw=True)


def report(res: Result) -> str:
    lines = ["## acceptance-guard", ""]
    if res.ok:
        lines.append("✅ Keine Verstöße.")
    for v in res.violations:
        lines.append(f"- ❌ {v}")
    for n in res.notes:
        lines.append(f"- ℹ️ {n}")
    return "\n".join(lines) + "\n"


def main() -> int:
    event = json.loads(open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8").read())
    pr = event["pull_request"]
    gh = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"],
                os.environ.get("GITHUB_API_URL", "https://api.github.com"))

    issues: dict[int, str | None] = {}
    for n in linked_issues(pr.get("body")):
        real, body = gh.issue_body(n)
        if real:
            issues[n] = body

    res = evaluate(
        files=gh.pr_files(pr["number"]),
        labels={lab["name"] for lab in pr.get("labels", [])},
        issues=issues,
        read_head_file=lambda p: gh.file_at(p, pr["head"]["sha"]),
    )
    if not issues:
        res.notes.append("PR verlinkt kein Issue („Closes #N“); Akzeptanz-Abgleich entfällt.")

    text = report(res)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    for v in res.violations:
        print(f"::error title=acceptance-guard::{v.splitlines()[0]}")
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
