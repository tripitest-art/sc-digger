"""DJ-Export (Kompatibilitaets-Shim): die Set-Logik lebt jetzt im Paket.

Harmonische Sortierung, Kantenkosten, Segmentbildung und die Dateiexporte
(M3U8/CSV/Report) stehen in `sc_digger/dj_export.py` - dort laufen sie unter
`python -m sc_digger.dj_export` und sind durch die Pakettests abgedeckt. Dieses
Skript haelt nur noch die alten Aufrufe am Leben:

  * `tests/test_dj_workflow.py` und `_eval/verify_dj_tests.py` importieren es als
    `dj_export` und erwarten die alten Namen: `Entry(key=...)`,
    `harmonic_relation`, `transition_cost` mit -1.0 als "unmoeglich",
    `order_harmonic`, `write_m3u8`, `write_csv`, `write_report`, `load_entries`.
  * `python _eval/dj_export.py ...` bleibt mit seinen Schaltern benutzbar
    (`--keys`, `--limit`, `--min-key-margin`, ...); gerechnet wird dabei
    ausschliesslich mit den Funktionen aus dem Paket.

Nicht delegiert ist die Audioanalyse: sie gehoert zu `_eval/analysis_v2.py` und
liefert Konfidenzen und Key-Marge, die das Paketmodul (Komfort fuer den
Tageslauf) nicht erhebt. Deshalb benutzt dieses Skript weiterhin analysis_v2.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Sowohl als Teil von _eval als auch aus dem Repo-Wurzelverzeichnis importierbar
# (verify_dj_tests.py startet mit sys.path[0] = _eval/).
_EVAL_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _EVAL_DIR.parent
for _p in (str(_REPO_ROOT), str(_EVAL_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import analysis_v2 as A  # noqa: E402  (Pfad oben gesetzt)
from sc_digger.dj_export import (  # noqa: E402,F401 - Altnamen weiterreichen
    AUDIO_EXT,
    BPM_COMFORT,
    BPM_HARD,
    BPM_PENALTY_PER_BPM,
    BPM_TOLERANCE,
    MAX_TRANSITION_COST,
    RELATION_LABEL,
    TRANSITION_COST,
    Playlist,
    camelot_relation,
    guess_artist_title,
    order_harmonic,
    parse_camelot,
    write_csv,
    write_m3u8,
    write_report,
)
from sc_digger.dj_export import Entry as _Entry  # noqa: E402
from sc_digger.dj_export import build_chain as _package_build_chain  # noqa: E402
from sc_digger.dj_export import load_entries as _package_load_entries  # noqa: E402
from sc_digger.dj_export import transition_cost as _package_transition_cost  # noqa: E402

log = logging.getLogger("dj_export")

# Frueher hiess die Beziehung im Werkzeug `harmonic_relation`.
harmonic_relation = camelot_relation


# ------------------------------------------------------------------ Datenmodell
class Entry(_Entry):
    """Alte Sicht auf Entry: die Tonart heisst hier weiterhin `key`.

    Die Felder sind die des Pakets; `key` ist nur ein Alias auf `camelot`, damit
    alter Code (`Entry(key="5A")`, `e.key = "6A"`) unveraendert weiterlaeuft.
    """

    def __init__(self, *, path: Path, artist: str = "", title: str = "",
                 bpm: float | None = None, bpm_confidence: float = 0.0,
                 bpm_octave: int = 1, key: str | None = None,
                 key_name: str | None = None, key_confidence: float = 0.0,
                 key_margin: float = 0.0, score: float | None = None,
                 duration_s: float | None = None) -> None:
        super().__init__(
            path=path, artist=artist, title=title, bpm=bpm,
            bpm_confidence=bpm_confidence, bpm_octave=bpm_octave,
            camelot=key, key_name=key_name, key_confidence=key_confidence,
            key_margin=key_margin,
            score=score if score is not None else 0.0,
            duration_s=duration_s if duration_s is not None else 0.0,
        )

    @property
    def key(self) -> str | None:
        return self.camelot

    @key.setter
    def key(self, value: str | None) -> None:
        self.camelot = value


def _as_legacy(entry: _Entry) -> Entry:
    """Paket-Entry in die alte Sicht umpacken."""
    return Entry(path=entry.path, artist=entry.artist, title=entry.title,
                 bpm=entry.bpm, bpm_confidence=entry.bpm_confidence,
                 bpm_octave=entry.bpm_octave, key=entry.camelot,
                 key_name=entry.key_name, key_confidence=entry.key_confidence,
                 key_margin=entry.key_margin, score=entry.score,
                 duration_s=entry.duration_s)


# ------------------------------------------------------------------ Uebergaenge
def transition_cost(a: Entry, b: Entry, bpm_tolerance: float = BPM_TOLERANCE,
                    bpm_penalty_per_bpm: float = BPM_PENALTY_PER_BPM) -> float:
    """Alte Signatur: nur die Kosten, -1.0 bedeutet "kein Uebergang".

    Im Paket sind Kosten ueber der Komfortgrenze `inf` und der BPM-Zuschlag ist
    fest 1.0 je BPM; `bpm_penalty_per_bpm` bleibt daher ungenutzt.
    """
    cost, _ = _package_transition_cost(a, b, bpm_tolerance)
    return -1.0 if cost == float("inf") else cost


def build_chain(pool: list[Entry], *, start: Entry | None = None,
                bpm_tolerance: float = BPM_TOLERANCE,
                bpm_penalty_per_bpm: float = BPM_PENALTY_PER_BPM,
                max_cost: float = MAX_TRANSITION_COST) -> list[Entry]:
    """Alte Signatur der Kettenbildung (siehe Entry.transition_cost)."""
    return _package_build_chain(pool, start=start, bpm_tolerance=bpm_tolerance,
                                max_cost=max_cost)


def load_entries(path: Path) -> list[Entry]:
    """Liest eine zuvor geschriebene CSV (Semikolon) als alte Entry-Objekte."""
    return [_as_legacy(e) for e in _package_load_entries(path)]


# ------------------------------------------------------------------ Analyse
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
    """Analysiert alle Audio-Dateien eines Ordners (ohne Duplikate).

    Bleibt bei analysis_v2: nur diese Analyse liefert Konfidenz und Key-Marge,
    mit denen der Report die verlaesslichen Mix-Anker zaehlt.
    """
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


# ------------------------------------------------------------------ CLI
def main() -> None:
    ap = argparse.ArgumentParser(
        prog="python _eval/dj_export.py",
        description="Harmonische Playlist aus analysierten Tracks "
                    "(Logik aus sc_digger.dj_export)",
        epilog="Beispiel: python _eval/dj_export.py --folder /music/inbox --out ./sets "
               "--name peaktime --bpm 150 165",
    )
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
