"""DJ-Export: aus Analyseergebnissen ein harmonisch sortiertes Set bauen.

Der Nutzen der Analyse entsteht nicht in `inbox/<BPM>/<Key>/`, sondern in dem, was
dein DJ-Programm liest. Dieses Modul macht aus einem Ordner analysierter Tracks:

  * `<name>.m3u8`  - Playlist in harmonischer Reihenfolge (Camelot + BPM)
  * `<name>.csv`   - alle Felder fuer Excel/Rekordbox/Skripte
  * `<name>.txt`   - Klartext-Report: Verteilung, unsichere Fälle, Problemzonen

Harmonische Nachbarschaft (Camelot):
  * gleicher Key                      -> optimal
  * +1 / -1 auf dem Rad, gleicher Modus -> sehr gut (energiearm -> energiehoch)
  * gleiche Zahl, anderer Buchstabe   -> relative Dur/Moll, gut
  * +2                                -> Energie-Boost, brauchbar
  * alles andere                      -> nur mit BPM-Uebergang sinnvoll

Aufruf:
  python _eval/dj_export.py --folder <ordner> --out <ziel> [--bpm 150 165]
                            [--no-analyze] [--limit 60] [--name set]
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analysis_v2 as A  # noqa: E402

log = logging.getLogger("dj_export")

AUDIO_EXT = {".wav", ".flac", ".aiff", ".aif", ".mp3", ".m4a"}

# Kantengewichte im harmonischen Graphen. Kleiner = besserer Uebergang.
TRANSITION_COST = {
    "gleich": 0.0,
    "rad_pm1": 1.0,
    "relativ": 1.5,
    "boost2": 3.0,
    "boost3": 5.0,
    "fern": 8.0,
    "unbekannt": 6.0,
}
BPM_TOLERANCE = 3.0        # bis hierher gilt ein Uebergang als "einfach"
BPM_COMFORT = 4.0          # darueber wird innerhalb eines Segments nicht mehr gemixt
BPM_HARD = 8.0             # darueber ist kein Uebergang mehr sinnvoll
BPM_PENALTY_PER_BPM = 1.0  # Zuschlag pro BPM ausserhalb der Toleranz
MAX_TRANSITION_COST = 3.0  # Gesamtkosten, ab denen geschnitten wird (Segmentgrenze)


# ------------------------------------------------------------------ Camelot
def parse_camelot(key: str | None) -> tuple[int, str] | None:
    """'5A' -> (5, 'A'). None bei unbekanntem Format."""
    if not key or len(key) < 2:
        return None
    num, letter = key[:-1], key[-1].upper()
    if letter not in ("A", "B") or not num.isdigit():
        return None
    n = int(num)
    if not 1 <= n <= 12:
        return None
    return n, letter


def harmonic_relation(a: str | None, b: str | None) -> str:
    """Beziehung zweier Camelot-Keys als Schluessel aus TRANSITION_COST."""
    pa, pb = parse_camelot(a), parse_camelot(b)
    if not pa or not pb:
        return "unbekannt"
    if pa == pb:
        return "gleich"
    na, la = pa
    nb, lb = pb
    if la == lb:
        diff = (nb - na) % 12
        if diff in (1, 11):
            return "rad_pm1"
        if diff == 2:
            return "boost2"
        if diff in (3, 9):
            return "boost3"
    if na == nb and la != lb:
        return "relativ"
    return "fern"


def transition_cost(a: "Entry", b: "Entry", bpm_tolerance: float = BPM_TOLERANCE,
                    bpm_penalty_per_bpm: float = BPM_PENALTY_PER_BPM) -> float:
    """Kosten eines Uebergangs a -> b (kleiner ist besser). -1 = unmoeglich.

    Der BPM-Anteil waechst linear mit dem Abstand und wird NICHT gedeckelt:
    Ein Deckel macht einen 5-BPM-Sprung so billig wie einen 4-BPM-Sprung und
    laesst ihn dadurch faelschlich als bequemen Uebergang durchgehen (genau dieser
    Fehler ist hier im Test aufgefallen). Oberhalb von BPM_COMFORT ist der
    Uebergang stattdessen unmoeglich - das ist die Segmentgrenze.
    """
    if a.bpm is not None and b.bpm is not None:
        dbpm = abs(a.bpm - b.bpm)
        if dbpm > BPM_COMFORT:
            return -1.0
        bpm_pen = max(0.0, dbpm - bpm_tolerance) * bpm_penalty_per_bpm
    else:
        bpm_pen = 1.0          # unbekanntes BPM ist ein Risiko, kein Ausschluss
    return TRANSITION_COST[harmonic_relation(a.key, b.key)] + bpm_pen


# ------------------------------------------------------------------ Datenmodell
@dataclass
class Entry:
    path: Path
    artist: str = ""
    title: str = ""
    bpm: float | None = None
    bpm_confidence: float = 0.0
    bpm_octave: int = 1
    key: str | None = None
    key_name: str | None = None
    key_confidence: float = 0.0
    key_margin: float = 0.0
    score: float | None = None
    duration_s: float | None = None

    @property
    def label(self) -> str:
        return f"{self.artist} - {self.title}".strip(" -") or self.path.stem

    @property
    def reliable_bpm(self) -> bool:
        return self.bpm is not None and self.bpm_confidence >= 0.5

    @property
    def reliable_key(self) -> bool:
        return self.key is not None and self.key_margin >= 0.05

    @property
    def is_anchor(self) -> bool:
        """Guter Mix-Punkt: Tempo und Tonart beide verlaesslich erkannt."""
        return self.reliable_bpm and self.reliable_key

    def to_dict(self) -> dict:
        return {
            "path": str(self.path), "artist": self.artist, "title": self.title,
            "bpm": self.bpm, "bpm_confidence": round(self.bpm_confidence, 3),
            "bpm_octave": self.bpm_octave, "camelot": self.key, "key_name": self.key_name,
            "key_confidence": round(self.key_confidence, 3),
            "key_margin": round(self.key_margin, 3),
            "reliable_bpm": self.reliable_bpm, "reliable_key": self.reliable_key,
            "score": self.score, "duration_s": self.duration_s,
        }


def guess_artist_title(stem: str) -> tuple[str, str]:
    for sep in (" - ", " – ", " — ", "-"):
        if sep in stem:
            a, _, t = stem.partition(sep)
            return a.strip(), t.strip()
    return "", stem.strip()


def duration_seconds(path: Path) -> float | None:
    import subprocess
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    try:
        return round(float(r.stdout.strip()), 1)
    except (ValueError, AttributeError):
        return None


def analyze_folder(folder: Path, *, snap_to: tuple[float, float] | None = None,
                   recursive: bool = True) -> list[Entry]:
    """Analysiert alle Audio-Dateien eines Ordners (ohne Duplikate)."""
    files = sorted(
        p for p in (folder.rglob("*") if recursive else folder.glob("*"))
        if p.is_file() and p.suffix.lower() in AUDIO_EXT
    )
    entries: list[Entry] = []
    for i, p in enumerate(files, 1):
        try:
            r = A.analyze(p, snap_to=snap_to)
        except Exception as e:  # noqa: BLE001
            log.warning("Analyse fehlgeschlagen fuer %s: %s", p.name, e)
            continue
        artist, title = guess_artist_title(p.stem)
        cands = r.get("key_candidates") or []
        entries.append(Entry(
            path=p, artist=artist, title=title,
            bpm=r.get("bpm"), bpm_confidence=r.get("bpm_confidence") or 0.0,
            bpm_octave=r.get("bpm_octave") or 1,
            key=r.get("key_camelot"), key_name=r.get("key_name"),
            key_confidence=r.get("key_confidence") or 0.0,
            key_margin=(cands[0]["r"] - cands[1]["r"]) if len(cands) > 1 else 0.0,
            duration_s=duration_seconds(p),
        ))
        log.info("[%d/%d] %s -> %s BPM (%s) %s", i, len(files), p.name,
                 entries[-1].bpm, entries[-1].key, entries[-1].key_name)
    return entries


# ------------------------------------------------------------------ Playlist
@dataclass
class Playlist:
    entries: list[Entry] = field(default_factory=list)
    transitions: list[dict] = field(default_factory=list)
    segments: int = 1


def build_chain(pool: list[Entry], *, start: Entry | None = None,
                bpm_tolerance: float = BPM_TOLERANCE,
                bpm_penalty_per_bpm: float = BPM_PENALTY_PER_BPM,
                max_cost: float = MAX_TRANSITION_COST) -> list[Entry]:
    """Kette aus Uebergaengen, die alle hoechstens `max_cost` kosten.

    Es wird nur ein Uebergang genommen, der wirklich bequem ist (Default:
    gleicher/benachbarter Key plus kleiner BPM-Unterschied). Reicht es nicht mehr,
    endet die Kette - lieber eine ehrliche ZaeSur als ein unharmonischer Sprung.
    """
    if not pool:
        return []
    start = start or pool[0]
    chain = [start]
    rest = [e for e in pool if e is not start]

    def cand_cost(cur: Entry, e: Entry) -> float:
        c = transition_cost(cur, e, bpm_tolerance, bpm_penalty_per_bpm)
        return c if c >= 0 else float("inf")

    while rest:
        cur = chain[-1]
        viable = [(cand_cost(cur, e), e) for e in rest]
        viable = [(c, e) for c, e in viable if c <= max_cost]
        if not viable:
            break
        if len(viable) > 1:
            # Bei fast gleichem Preis den Uebergang waehlen, von dem aus es
            # anschliessend am besten weitergeht (verhindert Sackgassen).
            def lookahead(item: tuple[float, Entry]) -> float:
                c, e = item
                others = [x for x in rest if x is not e]
                follow = min((cand_cost(e, x) for x in others), default=0.0)
                return c + 0.3 * min(follow, max_cost)
            cost, nxt = min(viable, key=lookahead)
        else:
            cost, nxt = viable[0]
        chain.append(nxt)
        rest.remove(nxt)
    return chain


def order_harmonic(entries: list[Entry], *, start: Entry | None = None,
                   bpm_tolerance: float = BPM_TOLERANCE,
                   bpm_penalty_per_bpm: float = BPM_PENALTY_PER_BPM,
                   max_cost: float = MAX_TRANSITION_COST) -> Playlist:
    """Harmonisch sortierte Playlist in Segmenten.

    Innerhalb eines Segments passt jeder Uebergang (Key-Abstand und BPM-Differenz
    im Rahmen). Wo das nicht mehr geht, beginnt ein neues Segment - das ist die
    Stelle, an der du im Set ohnehin einen Schnitt machst. Ein heimlicher
    BPM-Sprung mitten in der Liste waere fuer den DJ-Workflow schlimmer als eine
    sichtbare Segmentgrenze.
    """
    remaining = list(entries)
    if not remaining:
        return Playlist([], [], 0)

    segments: list[list[Entry]] = []
    while remaining:
        # Start jeweils nahe am Median-BPM des Rests (nicht am Rand)
        known = [e for e in remaining if e.bpm is not None]
        if start is None and known:
            med = float(np.median([e.bpm for e in known]))
            s = min(known, key=lambda e: abs(e.bpm - med))
        else:
            s = start if start in remaining else remaining[0]
        chain = build_chain(remaining, start=s, bpm_tolerance=bpm_tolerance,
                            bpm_penalty_per_bpm=bpm_penalty_per_bpm, max_cost=max_cost)
        if not chain:            # sollte nicht passieren, verhindert Endlosschleife
            chain = [remaining[0]]
        for e in chain:
            remaining.remove(e)
        segments.append(chain)
        start = None

    # Segmente nach fallendem Durchschnitts-BPM: Set startet druckvoll, baut ab
    def avg_bpm(seg: list[Entry]) -> float:
        vals = [e.bpm for e in seg if e.bpm is not None]
        return float(np.mean(vals)) if vals else 0.0

    segments.sort(key=avg_bpm, reverse=True)

    seq: list[Entry] = []
    transitions: list[dict] = []
    for si, seg in enumerate(segments, 1):
        if seq:
            last, first = seq[-1], seg[0]
            dbpm_cut = (None if (last.bpm is None or first.bpm is None)
                        else round(first.bpm - last.bpm, 1))
            transitions.append({"typ": "schnitt", "segment": si,
                                "von": last.label, "nach": first.label,
                                "harmonisch": harmonic_relation(last.key, first.key),
                                "kosten": None, "dbpm": dbpm_cut,
                                "warnung": "Segmentgrenze (kein direkter Uebergang)"})
        for a, b in zip(seg, seg[1:]):
            rel = harmonic_relation(a.key, b.key)
            dbpm = None if (a.bpm is None or b.bpm is None) else round(b.bpm - a.bpm, 1)
            if dbpm is None:
                warn = "BPM unbekannt"
            elif abs(dbpm) > BPM_COMFORT:
                # darf innerhalb eines Segments nicht vorkommen (Absicherung)
                warn = "BPM ausserhalb Komfort"
            elif abs(dbpm) > bpm_tolerance:
                warn = "BPM anpassen"
            elif rel in ("boost2", "boost3", "fern", "unbekannt"):
                warn = f"Key {rel}"
            else:
                warn = ""
            transitions.append({"typ": "uebergang", "segment": si,
                                "von": a.label, "nach": b.label, "harmonisch": rel,
                                "kosten": round(transition_cost(a, b, bpm_tolerance,
                                                                bpm_penalty_per_bpm), 2),
                                "dbpm": dbpm, "warnung": warn})
        seq.extend(seg)

    return Playlist(seq, transitions, len(segments))


# ------------------------------------------------------------------ Ausgabe
def write_m3u8(pl: Playlist, out: Path, *, absolute: bool = False,
               mark_segments: bool = True) -> Path:
    lines = ["#EXTM3U"]
    current_seg = None
    for e in pl.entries:
        seg = next((t["segment"] for t in pl.transitions
                    if t["typ"] == "uebergang" and t["nach"] == e.label), None)
        if mark_segments and seg is not None and seg != current_seg:
            current_seg = seg
            lines.append(f"# Segment {seg} (harmonisch fortlaufend)")
        secs = int(e.duration_s) if e.duration_s else -1
        info = f"{e.artist} - {e.title}" if e.artist else e.title
        tag = f"{e.key or '--'} · {e.bpm:.0f} BPM" if e.bpm else f"{e.key or '--'} · -- BPM"
        lines.append(f"#EXTINF:{secs},{info} [{tag}]")
        lines.append(str(e.path if absolute else e.path.name))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def write_csv(pl: Playlist, out: Path) -> Path:
    cols = ["pos", "artist", "title", "bpm", "bpm_confidence", "bpm_octave",
            "camelot", "key_name", "key_confidence", "key_margin",
            "reliable_bpm", "reliable_key", "duration_s", "score", "path"]
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(cols)
        for i, e in enumerate(pl.entries, 1):
            d = e.to_dict()
            d["pos"] = i
            w.writerow([d[c] for c in cols])
    return out


def write_report(entries: list[Entry], pl: Playlist, out: Path,
                 *, bpm_tolerance: float = BPM_TOLERANCE) -> Path:
    n = len(entries)
    with_bpm = [e for e in entries if e.bpm is not None]
    with_key = [e for e in entries if e.key]
    rel_bpm = [e for e in entries if e.reliable_bpm]
    rel_key = [e for e in entries if e.reliable_key]
    octaves = [e for e in entries if e.bpm_octave != 1]

    lines = [
        "sc-digger DJ-Export - Report",
        "=" * 60,
        f"Tracks insgesamt          : {n}",
        f"BPM erkannt               : {len(with_bpm)}/{n} ({100*len(with_bpm)/max(n,1):.0f} %)",
        f"davon verlaesslich (>=0.5): {len(rel_bpm)}",
        f"Key erkannt               : {len(with_key)}/{n} ({100*len(with_key)/max(n,1):.0f} %)",
        f"davon verlaesslich (Marge): {len(rel_key)}",
        f"Mix-Anker (BPM+Key sicher): {sum(1 for e in entries if e.is_anchor)}",
        f"Oktav-Hinweis (halb/dopp.) : {len(octaves)}",
        "",
        "Playlist",
        "-" * 60,
        f"Laenge                    : {len(pl.entries)}",
        f"Segmente                  : {pl.segments}",
    ]
    edges = [t for t in pl.transitions if t["typ"] == "uebergang"]
    cuts = [t for t in pl.transitions if t["typ"] == "schnitt"]
    warns = [t for t in edges if t["warnung"]]
    lines.append(f"Uebergaenge mit Hinweis   : {len(warns)} von {len(edges)}")
    rel_counts: dict[str, int] = {}
    for t in edges:
        rel_counts[t["harmonisch"]] = rel_counts.get(t["harmonisch"], 0) + 1
    lines.append("Harmonische Verteilung    : " + ", ".join(
        f"{k}={v}" for k, v in sorted(rel_counts.items(), key=lambda kv: -kv[1])))

    # Verteilung der PLAYLIST, nicht des gesamten Ordners - sonst zeigt der Report
    # Zahlen, die mit der erzeugten Liste nichts zu tun haben.
    pl_bpm = [e.bpm for e in pl.entries if e.bpm is not None]
    pl_key = [e for e in pl.entries if e.key]
    if pl_bpm:
        lines += ["", f"BPM-Bereich (Playlist)    : {min(pl_bpm):.0f} - {max(pl_bpm):.0f}",
                  f"BPM-Median (Playlist)     : {np.median(pl_bpm):.0f}"]
    if pl_key:
        kc: dict[str, int] = {}
        for e in pl_key:
            kc[e.key] = kc.get(e.key, 0) + 1
        lines.append("Keys in der Playlist      : " + ", ".join(
            f"{k}({v})" for k, v in sorted(kc.items(), key=lambda kv: -kv[1])))
    if not pl.entries:
        lines.append("Hinweis                   : kein Track erfuellt die Filter")

    unsicher = [e for e in entries if not e.reliable_key or not e.reliable_bpm]
    if unsicher:
        lines += ["", "Unsicher / manuell pruefen", "-" * 60]
        for e in unsicher[:40]:
            grund = []
            if not e.reliable_bpm:
                grund.append(f"BPM {e.bpm if e.bpm else '--'} (conf {e.bpm_confidence:.2f})")
            if not e.reliable_key:
                grund.append(f"Key {e.key or '--'} (Marge {e.key_margin:.3f})")
            lines.append(f"  {e.label[:60]:60s} {'; '.join(grund)}")

    if pl.transitions:
        lines += ["", "Uebergaenge", "-" * 60]
        for i, t in enumerate(pl.transitions, 1):
            if t["typ"] == "schnitt":
                lines.append(f"  --- {i:3d}. SEGMENT {t['segment']}: Schnitt "
                             f"{t['von'][:26]:26s} -> {t['nach'][:26]:26s}")
                continue
            warn = f"  <-- {t['warnung']}" if t["warnung"] else ""
            lines.append(f"  {i:3d}. {t['von'][:30]:30s} -> {t['nach'][:30]:30s} "
                         f"{t['harmonisch']:9s} dBPM {str(t['dbpm']):>6s}{warn}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def load_entries(path: Path) -> list[Entry]:
    """Liest eine zuvor geschriebene CSV wieder ein (Analyse nicht wiederholen)."""
    out = []
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            out.append(Entry(
                path=Path(row["path"]), artist=row["artist"], title=row["title"],
                bpm=float(row["bpm"]) if row["bpm"] else None,
                bpm_confidence=float(row["bpm_confidence"] or 0),
                bpm_octave=int(row["bpm_octave"] or 1),
                key=row["camelot"] or None, key_name=row["key_name"] or None,
                key_confidence=float(row["key_confidence"] or 0),
                key_margin=float(row["key_margin"] or 0),
                duration_s=float(row["duration_s"]) if row["duration_s"] else None,
            ))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Harmonische Playlist aus analysierten Tracks",
        epilog="Beispiel: python dj_export.py --folder /music/inbox --out ./sets "
               "--name peaktime --bpm 150 165")
    ap.add_argument("--folder", required=True,
                    help="Ordner mit Audio-Dateien - ODER eine zuvor erzeugte CSV "
                         "(dann wird nicht neu analysiert)")
    ap.add_argument("--out", default=".", help="Zielordner fuer Playlist/CSV/Report")
    ap.add_argument("--name", default="set", help="Basisname der Ausgabedateien")
    ap.add_argument("--bpm", nargs=2, type=float, metavar=("MIN", "MAX"),
                    help="BPM-Fenster; nur Tracks darin kommen in die Playlist")
    ap.add_argument("--keys", nargs="*", help="nur diese Camelot-Keys (z.B. 4A 5A 5B)")
    ap.add_argument("--min-key-margin", type=float, default=0.0,
                    help="unsichere Keys ausschliessen")
    ap.add_argument("--bpm-tolerance", type=float, default=BPM_TOLERANCE,
                    help=f"BPM-Abstand, der als unkritisch gilt (Default {BPM_TOLERANCE})")
    ap.add_argument("--max-cost", type=float, default=MAX_TRANSITION_COST,
                    help=f"Kostengrenze je Uebergang, sonst Segmentgrenze "
                         f"(Default {MAX_TRANSITION_COST})")
    ap.add_argument("--limit", type=int, default=0, help="max. Tracks (0 = alle)")
    ap.add_argument("--from-csv", action="store_true",
                    help="erzwingt CSV statt Analyse (wird bei *.csv automatisch erkannt)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    src = Path(a.folder)
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)

    # CSV automatisch erkennen: spart die Re-Analyse und den zweiten Schalter
    use_csv = a.from_csv or (src.is_file() and src.suffix.lower() == ".csv")
    if use_csv:
        if not src.is_file():
            ap.error(f"--from-csv erwartet eine CSV-Datei, nicht {src}")
        entries = load_entries(src)
        log.info("%d Eintraege aus %s geladen (keine Neuanalyse)", len(entries), src.name)
    else:
        if not src.is_dir():
            ap.error(f"Ordner nicht gefunden: {src}")
        entries = analyze_folder(src)

    candidates = list(entries)
    if a.bpm:
        lo, hi = a.bpm
        candidates = [e for e in candidates if e.bpm is not None and lo <= e.bpm <= hi]
    if a.keys:
        wanted = {k.upper() for k in a.keys}
        candidates = [e for e in candidates if (e.key or "").upper() in wanted]
    if a.min_key_margin > 0:
        candidates = [e for e in candidates if e.key_margin >= a.min_key_margin]

    pl = order_harmonic(candidates, bpm_tolerance=a.bpm_tolerance, max_cost=a.max_cost)
    if a.limit and len(pl.entries) > a.limit:
        # zusammenhängenden Anfang nehmen und die Uebergaenge neu protokollieren
        pl = order_harmonic(pl.entries[:a.limit], bpm_tolerance=a.bpm_tolerance,
                            max_cost=a.max_cost)

    m3u = write_m3u8(pl, outdir / f"{a.name}.m3u8")
    csv_path = write_csv(pl, outdir / f"{a.name}.csv")
    rep = write_report(entries, pl, outdir / f"{a.name}_report.txt")
    print(f"Playlist : {m3u}\nCSV      : {csv_path}\nReport   : {rep}")
    print(f"\n{len(pl.entries)} Tracks in der Playlist (von {len(entries)} analysierten)")


if __name__ == "__main__":
    main()
