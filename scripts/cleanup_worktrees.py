#!/usr/bin/env python3
"""Wartungsskript: Worktrees gemergter PRs aufräumen (Issue #558).

Wir mergen per Squash, der Branch wird dadurch nie Vorfahre von main. Darum taugen
`git branch --merged` und `merge-base --is-ancestor` hier nicht; die Wahrheit steht nur
in den PRs. Entfernt werden nur Worktrees, deren Branch zu einem gemergten PR gehört.
Branches bleiben als Verlauf stehen.

Verwendung:
    python scripts/cleanup_worktrees.py --repo tripitest-art/sc-digger \\
        --root /home/dev/orca/workspaces/sc-digger [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def merged_branches(repo: str) -> set[str]:
    """Branch-Namen aller PRs im Zustand MERGED (gh pr list --state merged)."""
    out = subprocess.run(
        ["gh", "pr", "list", "--repo", repo, "--state", "merged", "--limit", "1000",
         "--json", "headRefName"],
        check=True, capture_output=True, text=True,
    ).stdout
    return {pr["headRefName"] for pr in json.loads(out)}


def plan(worktrees: list[tuple[str, str]], merged: set[str],
         *, protect: tuple[str, ...] = ("main", "master")) -> list[str]:
    """worktrees = [(pfad, branch)]. Liefert die Pfade, die weg können:
    Branch in merged, nicht in protect, Pfad existiert."""
    return [pfad for pfad, branch in worktrees
            if branch in merged and branch not in protect and os.path.exists(pfad)]


def list_worktrees(root: str | Path) -> list[tuple[str, str]]:
    """Liest `git worktree list --porcelain` im Ordner root als [(pfad, branch)].
    Worktrees ohne Branch (detached) werden übersprungen, damit nie etwas Unklares fällt."""
    out = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout
    result: list[tuple[str, str]] = []
    pfad = ""
    for zeile in out.splitlines() + [""]:
        if zeile.startswith("worktree "):
            pfad = zeile[len("worktree "):]
        elif zeile.startswith("branch "):
            result.append((pfad, zeile[len("branch "):].removeprefix("refs/heads/")))
    return result


def remove_worktrees(repo, paths: list[str], *, dry_run: bool = False) -> list[str]:
    """Entfernt die Worktrees (git worktree remove --force) und gibt die entfernten
    Pfade zurück. Bei dry_run: nichts entfernen, leere Liste. Branches bleiben."""
    if dry_run:
        return []
    entfernt: list[str] = []
    for pfad in paths:
        subprocess.run(["git", "worktree", "remove", "--force", pfad],
                       cwd=repo, check=True, capture_output=True, text=True)
        entfernt.append(pfad)
    return entfernt


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True, help="GitHub-Repo, z. B. tripitest-art/sc-digger")
    ap.add_argument("--root", required=True, help="Ordner eines Klons/Worktrees des Repos")
    ap.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts entfernen")
    args = ap.parse_args(argv)

    alle = list_worktrees(args.root)
    weg = plan(alle, merged_branches(args.repo))
    behalten = [p for p, _ in alle if p not in weg]
    print(f"{len(weg)} Worktree(s) gemergter PRs, {len(behalten)} bleiben:")
    for pfad in weg:
        print(f"  weg:    {pfad}")
    for pfad in behalten:
        print(f"  bleibt: {pfad}")
    if args.dry_run:
        print("Trockenlauf: nichts entfernt.")
        return 0
    entfernt = remove_worktrees(args.root, weg)
    print(f"{len(entfernt)} Worktree(s) entfernt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
