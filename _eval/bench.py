"""Vergleich: Repo-Verfahren (Numpy-Nachbau) vs. Kandidat v2, auf dem Ground-Truth-Korpus.

Aufruf:  python _eval/bench.py [mix|padonly|drumsonly]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL))

import analysis_v2 as v2              # noqa: E402
import repo_baseline as base          # noqa: E402

# Zielfenster fuers Oktav-Snapping: aus der Config (search.bpm_min/max), hier
# fest, damit der Benchmark ohne config.yaml laeuft.
SNAP_TO = (150.0, 165.0)

TRUTH_PATH = EVAL / "corpus" / "truth.json"
if not TRUTH_PATH.exists():
    raise SystemExit(
        f"Ground-Truth-Korpus fehlt: {TRUTH_PATH}\n"
        f"Erst erzeugen:  python {Path(__file__).parent.name}/gen_corpus.py"
    )
TRUTH = json.loads(TRUTH_PATH.read_text(encoding="utf-8"))


def bpm_error(detected, truth) -> tuple[float, str]:
    """Kleinstes Fehlermass gegen T, T/2, 2T. Oktavfehler werden separat gezaehlt."""
    if not detected:
        return float("inf"), "fail"
    err, factor = min((abs(detected - truth * f), f) for f in (0.5, 1.0, 2.0))
    if err <= 2.0:
        return err, ("ok" if factor == 1.0 else f"oct{factor:g}")
    return err, "wrong"


def main() -> None:
    variant = sys.argv[1] if len(sys.argv) > 1 else "mix"
    if variant not in ("mix", "padonly", "drumsonly"):
        raise SystemExit("Korpus-Variante waehlen: mix | padonly | drumsonly")
    sub = EVAL / "corpus" / (variant if variant != "mix" else "")
    # Das Snappen ist nur sinnvoll, wenn ueberhaupt ein Puls da ist: auf einem
    # reinen Pad gibt es keinen Beat, den man in ein Fenster holen koennte.
    snap = SNAP_TO if variant != "padonly" else None
    rows = []
    t_all = time.time()
    for item in TRUTH:
        p = sub / f"{item['name']}.wav"
        row = {"name": item["name"], "truth_bpm": item["bpm"], "truth_key": item["key_camelot"],
               "note": item.get("note", "")}

        # --- Repo-Verfahren (jeweils eigener ffmpeg-Decode + eigene STFT) ---
        t0 = time.time()
        y_base = base.decode(p, duration=60, offset=30)
        row["repo_bpm"] = base.detect_bpm_repo(y_base) if y_base is not None else None
        row["repo_key"], row["repo_key_name"], row["repo_key_r"] = (
            base.detect_key_repo(y_base) if y_base is not None else (None, None, 0.0))
        row["repo_ms"] = round((time.time() - t0) * 1000)

        # --- Kandidat v2: ein Decode, beide Features ---
        t0 = time.time()
        y = v2.decode(p)
        if y is None:
            row["v2_bpm"] = row["v2_key"] = None
            row["v2_ms"] = round((time.time() - t0) * 1000)
            rows.append(row)
            continue
        tr = v2.detect_tempo(y, snap_to=snap)
        kr = v2.detect_key(y)
        row.update(v2_bpm=tr.bpm, v2_conf=round(tr.confidence, 2), v2_oct=tr.octave,
                   v2_key=kr["camelot"], v2_key_name=kr["key_name"],
                   v2_key_conf=kr["mode_conf"], v2_key_margin=kr["margin"])
        row["v2_ms"] = round((time.time() - t0) * 1000)
        rows.append(row)
        print(f"  {item['name']:22s} repo {str(row.get('repo_bpm')):>7s} | "
              f"v2 {str(row.get('v2_bpm')):>7s}   keys repo {row.get('repo_key')} / "
              f"v2 {row.get('v2_key')} (soll {item['key_camelot']})", flush=True)

    # ---------------- Auswertung ----------------
    def bpm_stats(col: str) -> tuple[int, int, int, int]:
        ok = oct_ = wrong = fail = 0
        for r in rows:
            v = r.get(col)
            if not isinstance(v, (int, float)):
                fail += 1
                continue
            _, cat = bpm_error(v, r["truth_bpm"])
            if cat == "ok":
                ok += 1
            elif cat.startswith("oct"):
                oct_ += 1
            else:
                wrong += 1
        return ok, oct_, wrong, fail

    n = len(rows)
    print(f"\n=== Korpus '{variant}' ({n} Cases) ===")
    print(f"{'Kennzahl':34s}{'Repo-Verfahren':>18s}{'Kandidat v2':>16s}")
    ok_r, oct_r, wr_r, fl_r = bpm_stats("repo_bpm")
    ok_v, oct_v, wr_v, fl_v = bpm_stats("v2_bpm")
    print(f"{'BPM exakt (Toleranz 2)':34s}{ok_r:>18d}{ok_v:>16d}")
    print(f"{'BPM nur Oktavfehler':34s}{oct_r:>18d}{oct_v:>16d}")
    print(f"{'BPM falsch':34s}{wr_r:>18d}{wr_v:>16d}")
    print(f"{'BPM keine Erkennung':34s}{fl_r:>18d}{fl_v:>16d}")
    print(f"{'BPM exakt oder Oktave [%]':34s}{100*(ok_r+oct_r)/n:>18.0f}{100*(ok_v+oct_v)/n:>16.0f}")

    ke_r = sum(1 for r in rows if r.get("repo_key") == r["truth_key"])
    ke_v = sum(1 for r in rows if r.get("v2_key") == r["truth_key"])
    print(f"{'Key exakt':34s}{ke_r:>18d}{ke_v:>16d}")
    print(f"{'Key exakt [%]':34s}{100*ke_r/n:>18.0f}{100*ke_v/n:>16.0f}")
    # relative Dur/Moll-Verwechslung (Camelot: gleiche Zahl, anderer Buchstabe)
    rel_r = sum(1 for r in rows if r.get("repo_key") and r["truth_key"]
                and r["repo_key"][:-1] == r["truth_key"][:-1]
                and r["repo_key"][-1] != r["truth_key"][-1])
    rel_v = sum(1 for r in rows if r.get("v2_key") and r["truth_key"]
                and r["v2_key"][:-1] == r["truth_key"][:-1]
                and r["v2_key"][-1] != r["truth_key"][-1])
    print(f"{'davon rel. Dur/Moll-Verwechslung':34s}{rel_r:>18d}{rel_v:>16d}")

    med_r = int(np.median([r["repo_ms"] for r in rows]))
    med_v = int(np.median([r["v2_ms"] for r in rows]))
    print(f"{'Rechenzeit pro Track [ms] (Median)':34s}{med_r:>18d}{med_v:>16d}")

    print(f"\n=== Detail BPM ===")
    print(f"{'case':24s}{'soll':>6s}{'repo':>9s}{'':4s}{'v2':>9s}{'':4s}{'v2-conf':>9s}{'oct':>5s}")
    for r in rows:
        def mark(v):
            if not isinstance(v, (int, float)):
                return "  --"
            _, cat = bpm_error(v, r["truth_bpm"])
            return "  ok" if cat == "ok" else ("  oct" if cat.startswith("oct") else "  !")
        print(f"{r['name']:24s}{r['truth_bpm']:6.0f}{str(r.get('repo_bpm')):>9s}{mark(r.get('repo_bpm'))}"
              f"{str(r.get('v2_bpm')):>9s}{mark(r.get('v2_bpm'))}"
              f"{str(r.get('v2_conf')):>9s}{str(r.get('v2_oct')):>5s}")

    print(f"\n=== Detail Key ===")
    print(f"{'case':24s}{'soll':>5s}{'repo':>8s}{'':3s}{'v2':>8s}{'':3s}{'v2-marge':>10s}")
    for r in rows:
        print(f"{r['name']:24s}{r['truth_key']:>5s}{str(r.get('repo_key')):>8s}"
              f"{'  ok' if r.get('repo_key') == r['truth_key'] else '  --'}"
              f"{str(r.get('v2_key')):>8s}"
              f"{'  ok' if r.get('v2_key') == r['truth_key'] else '  --'}"
              f"{str(r.get('v2_key_margin')):>10s}")

    out = EVAL / f"results_{variant}.json"
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nRohdaten: {out}   Gesamt {time.time() - t_all:.0f}s")


if __name__ == "__main__":
    main()
