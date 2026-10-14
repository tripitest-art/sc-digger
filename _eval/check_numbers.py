"""Gegenprobe: stimmen die Zahlen in Commit-Message und Bericht mit den Rohdaten?"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

EVAL = Path(__file__).resolve().parent
for name, label in (("results_mix.json", "mix"), ("results_padonly.json", "nur Pad")):
    rows = json.loads((EVAL / name).read_text(encoding="utf-8"))
    n = len(rows)
    repo_bpm = sum(1 for r in rows if isinstance(r.get("repo_bpm"), (int, float))
                   and abs(r["repo_bpm"] - r["truth_bpm"]) <= 2)
    repo_key = sum(1 for r in rows if r.get("repo_key") == r["truth_key"])
    v2_bpm = sum(1 for r in rows if isinstance(r.get("v2_bpm"), (int, float))
                 and abs(r["v2_bpm"] - r["truth_bpm"]) <= 2)
    v2_key = sum(1 for r in rows if r.get("v2_key") == r["truth_key"])
    ms_repo = int(np.median([r["repo_ms"] for r in rows]))
    ms_v2 = int(np.median([r["v2_ms"] for r in rows]))
    print(f"{label:8s} n={n}  Repo: BPM {repo_bpm}/{n}, Key {repo_key}/{n}, {ms_repo} ms"
          f"  |  v2: BPM {v2_bpm}/{n}, Key {v2_key}/{n}, {ms_v2} ms")
