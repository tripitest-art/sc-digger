#!/usr/bin/env python3
"""Entwicklungs-Ablauf aus GitHub-Labels sichtbar machen.

Wertet offene Issues und Pull Requests aus, ordnet sie nach Status
(Idee, Bereit, Blockiert, In Arbeit, Fertig) und erkennt hängende Fälle
(PR ohne Review, blockiert aber Abhängigkeiten geschlossen, in-arbeit ohne PR,
Regel 8: mehrere Läufe an sc_digger/main.py gleichzeitig).
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Iterable, Sequence

# Projekt-Root zum sys.path hinzufügen, falls das Skript direkt ausgeführt wird
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


@dataclass(frozen=True)
class Befund:
    art: str     # "pr-ohne-review" | "blockiert-frei" | "in-arbeit-ohne-pr" | "regel-8"
    gewicht: int # 1 = Hinweis, 2 = Handlung nötig
    satz: str    # fertiger deutscher Satz für die Ausgabe


STATUS_REIHENFOLGE = ("idee", "bereit", "blockiert", "in-arbeit", "fertig")

STATUS_TITEL = {
    "idee": "Idee",
    "bereit": "Bereit",
    "blockiert": "Blockiert",
    "in-arbeit": "In Arbeit",
    "fertig": "Fertig",
}


def _get_label_names(item: dict) -> set[str]:
    """Extrahiert die Label-Namen aus einem Issue- oder PR-Dictionary."""
    labels = set()
    for lbl in item.get("labels", []):
        if isinstance(lbl, dict):
            name = lbl.get("name")
            if name:
                labels.add(name)
        elif isinstance(lbl, str):
            labels.add(lbl)
    return labels


def status_of(issue: dict) -> str:
    """„in-arbeit" vor „blockiert" vor „bereit" vor „idee", sonst „ohne-status".
    Labels werden als [{"name": ...}] erwartet (Format von `gh issue list --json labels`)."""
    labels = _get_label_names(issue)
    for s in ("in-arbeit", "blockiert", "bereit", "idee", "fertig"):
        if s in labels:
            return s
    return "ohne-status"


def wartet_auf(body: str | None) -> list[int]:
    """Zahlen aus einer Zeile „Wartet auf: #12, #34" (Groß-/Kleinschreibung egal), sonst []."""
    if not body:
        return []
    for line in body.splitlines():
        m = re.search(r"wartet\s+auf\s*:\s*(.*)", line, re.IGNORECASE)
        if m:
            nums = re.findall(r"\d+", m.group(1))
            return [int(n) for n in nums]
    return []


def haengende_faelle(issues: list[dict], prs: list[dict], *, now: str,
                     geschlossen: Iterable[int] = ()) -> list[Befund]:
    """Alle vier Regeln, sortiert: erst gewicht 2, dann 1; innerhalb nach Nummer."""
    geschlossen_set = set(geschlossen)
    candidates: list[tuple[int, int, Befund]] = []

    # Parse Bezugszeit
    t_now = datetime.fromisoformat(now.replace("Z", "+00:00"))

    # Regel 1: pr-ohne-review (Gewicht 2)
    # PR ist offen, isDraft ist falsch, reviewDecision ist leer oder REVIEW_REQUIRED,
    # und createdAt liegt mehr als 2 Tage vor now.
    # Satz: „PR #<n> wartet seit <x> Tagen ohne Review-Urteil“.
    open_prs: list[dict] = []
    for pr in prs:
        state = pr.get("state", "OPEN")
        if isinstance(state, str) and state.upper() not in ("OPEN", ""):
            continue
        open_prs.append(pr)

        if pr.get("isDraft", False):
            continue
        review_decision = (pr.get("reviewDecision") or "").strip().upper()
        if review_decision not in ("", "REVIEW_REQUIRED"):
            continue
        created_at_str = pr.get("createdAt")
        if not created_at_str:
            continue
        t_created = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        delta = t_now - t_created
        if delta.total_seconds() > 2 * 86400:
            pr_nr = pr.get("number", 0)
            satz = f"PR #{pr_nr} wartet seit {delta.days} Tagen ohne Review-Urteil"
            candidates.append((2, pr_nr, Befund(art="pr-ohne-review", gewicht=2, satz=satz)))

    # Regel 2: blockiert-frei (Gewicht 2)
    # Issue mit Label blockiert, wartet_auf(body) ist nicht leer, und alle diese Nummern stehen in geschlossen.
    # Satz: „Issue #<n> kann freigegeben werden (wartet auf #a, #b — alle erledigt)“.
    for issue in issues:
        labels = _get_label_names(issue)
        if "blockiert" in labels:
            deps = wartet_auf(issue.get("body"))
            if deps and all(d in geschlossen_set for d in deps):
                issue_nr = issue.get("number", 0)
                dep_str = ", ".join(f"#{d}" for d in deps)
                satz = f"Issue #{issue_nr} kann freigegeben werden (wartet auf {dep_str} — alle erledigt)"
                candidates.append((2, issue_nr, Befund(art="blockiert-frei", gewicht=2, satz=satz)))

    # Regel 3: in-arbeit-ohne-pr (Gewicht 2)
    # Issue in-arbeit, kein offener PR mit headRefName, das auf feature/issue-<n> beginnt.
    # Satz: „Issue #<n> hängt auf in-arbeit (kein offener PR)“.
    for issue in issues:
        labels = _get_label_names(issue)
        if "in-arbeit" in labels:
            issue_nr = issue.get("number", 0)
            has_matching_pr = any(
                bool(re.match(rf"^feature/issue-{issue_nr}(?:[^0-9]|$)", pr.get("headRefName") or ""))
                for pr in open_prs
            )
            if not has_matching_pr:
                satz = f"Issue #{issue_nr} hängt auf in-arbeit (kein offener PR)"
                candidates.append((2, issue_nr, Befund(art="in-arbeit-ohne-pr", gewicht=2, satz=satz)))

    # Regel 4: regel-8 (Gewicht 1)
    # Mindestens zwei Issues mit in-arbeit und Label berührt-main.py.
    # Ein Befund mit allen Nummern: „Regel 8: #a und #b arbeiten gleichzeitig an sc_digger/main.py“.
    r8_issues = [
        iss for iss in issues
        if "in-arbeit" in _get_label_names(iss) and "berührt-main.py" in _get_label_names(iss)
    ]
    if len(r8_issues) >= 2:
        nums = sorted(iss.get("number", 0) for iss in r8_issues)
        if len(nums) == 2:
            nums_str = f"#{nums[0]} und #{nums[1]}"
        else:
            nums_str = ", ".join(f"#{n}" for n in nums[:-1]) + f" und #{nums[-1]}"
        satz = f"Regel 8: {nums_str} arbeiten gleichzeitig an sc_digger/main.py"
        candidates.append((1, nums[0], Befund(art="regel-8", gewicht=1, satz=satz)))

    # Sortierung: erst gewicht 2, dann 1; innerhalb nach Nummer
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in candidates]


def uebersicht_markdown(issues: list[dict], prs: list[dict], *, now: str,
                        geschlossen: Iterable[int] = ()) -> str:
    """Überschrift, je Status eine Aufzählung „- #12 Titel", dann Abschnitt „Hängende Fälle".
    Ohne Fälle steht dort „- keine"."""
    lines = ["# Entwicklungs-Ablauf", ""]
    for st in STATUS_REIHENFOLGE:
        st_issues = [iss for iss in issues if status_of(iss) == st]
        if not st_issues:
            continue
        lines.append(f"## {STATUS_TITEL[st]}")
        for iss in sorted(st_issues, key=lambda x: x.get("number", 0)):
            lines.append(f"- #{iss.get('number')} {iss.get('title')}")
        lines.append("")

    lines.append("## Hängende Fälle")
    faelle = haengende_faelle(issues, prs, now=now, geschlossen=geschlossen)
    if faelle:
        for f in faelle:
            lines.append(f"- {f.satz}")
    else:
        lines.append("- keine")

    return "\n".join(lines).strip() + "\n"


def _run_gh(args: Sequence[str]) -> list[dict]:
    """Führt ein gh-Kommando aus und liefert das geparste JSON-Ergebnis zurück."""
    cmd = ["gh", *args]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        raise RuntimeError("GitHub CLI ('gh') ist nicht installiert oder nicht im PATH.")
    except subprocess.CalledProcessError as e:
        err = e.stderr.strip() if e.stderr else f"Exit-Code {e.returncode}"
        raise RuntimeError(f"Aufruf von '{' '.join(cmd)}' fehlgeschlagen: {err}")
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Ungültige JSON-Ausgabe von '{' '.join(cmd)}': {e}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Übersicht des Entwicklungs-Ablaufs aus GitHub-Labels")
    parser.add_argument("--issues", help="Pfad zu JSON-Datei mit offenen Issues")
    parser.add_argument("--prs", help="Pfad zu JSON-Datei mit offenen PRs")
    parser.add_argument("--geschlossen", help="Pfad zu JSON-Datei mit geschlossenen Issues")
    parser.add_argument("--format", choices=["md", "json"], default="md", help="Ausgabeformat: md (Standard) oder json")
    parser.add_argument("--now", help="ISO-Zeit für Auswertung (Standard: aktuelle Zeit)")

    args = parser.parse_args(argv)

    now = args.now or datetime.now(timezone.utc).isoformat()

    try:
        if args.issues and args.prs:
            with open(args.issues, "r", encoding="utf-8") as f:
                issues = json.load(f)
            with open(args.prs, "r", encoding="utf-8") as f:
                prs = json.load(f)
            if args.geschlossen:
                with open(args.geschlossen, "r", encoding="utf-8") as f:
                    closed_raw = json.load(f)
                geschlossen = {item["number"] if isinstance(item, dict) else item for item in closed_raw}
            else:
                geschlossen = ()
        else:
            issues = _run_gh([
                "issue", "list", "--state", "open", "--limit", "200",
                "--json", "number,title,labels,body,updatedAt"
            ])
            prs = _run_gh([
                "pr", "list", "--state", "open", "--limit", "200",
                "--json", "number,title,labels,headRefName,reviewDecision,isDraft,createdAt"
            ])
            closed_raw = _run_gh([
                "issue", "list", "--state", "closed", "--limit", "200",
                "--json", "number"
            ])
            geschlossen = {item["number"] for item in closed_raw if isinstance(item, dict) and "number" in item}
    except RuntimeError as e:
        sys.stderr.write(f"Fehler: {e}\n")
        return 2

    if args.format == "json":
        faelle = haengende_faelle(issues, prs, now=now, geschlossen=geschlossen)
        print(json.dumps([asdict(f) for f in faelle], indent=2, ensure_ascii=False))
    else:
        print(uebersicht_markdown(issues, prs, now=now, geschlossen=geschlossen))

    return 0


if __name__ == "__main__":
    sys.exit(main())
