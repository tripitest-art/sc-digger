"""DJ-Export: aus Analyseergebnissen ein harmonisch sortiertes Set bauen.

Der Nutzen der Analyse entsteht nicht in `inbox/<BPM>/<Key>/`, sondern in dem, was
das DJ-Programm liest. Dieses Modul macht aus einem Ordner analysierter Tracks
(oder aus einer zuvor erzeugten CSV):

  * `<name>.m3u8`        - Playlist in harmonischer Reihenfolge (Camelot + BPM)
  * `<name>.csv`         - alle Felder fuer Excel/Rekordbox/Skripte
  * `<name>_report.txt`  - Pruefbericht: Verteilung, Problemzonen, Uebergaenge

Harmonische Nachbarschaft (Camelot):
  * gleicher Key                          -> optimal
  * +1 / -1 auf dem Rad, gleicher Modus   -> sehr gut (energiearm -> energiehoch)
  * gleiche Zahl, anderer Buchstabe       -> relative Dur/Moll, gut
  * +2                                    -> Energie-Boost, brauchbar
  * alles andere                          -> nur mit BPM-Uebergang sinnvoll

Aufruf:
  python -m sc_digger.dj_export --folder <ordner> --out <ziel> [--bpm 150 165]
                                [--name set] [--from-csv]

Die Logik stammt aus dem Evaluationswerkzeug `_eval/dj_export.py`; dort steht nur
noch ein Kompatibilitaets-Shim, damit alte Aufrufe weiterlaufen.
"""
from __future__ import annotations

import argparse
import csv
import logging
import statistics
from dataclasses import dataclass, field
from pathlib import Path

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
# Lesbare Einstufung derselben Beziehungen (zweiter Rueckgabewert von transition_cost).
RELATION_LABEL = {
    "gleich": "optimal",
    "rad_pm1": "sehr gut",
    "relativ": "gut",
    "boost2": "Energie-Boost",
    "boost3": "weiter Boost",
    "fern": "unharmonisch",
    "unbekannt": "Key unbekannt",
}
BPM_TOLERANCE = 3.0        # bis hierher gilt ein Uebergang als "einfach"
BPM_COMFORT = 4.0          # darueber wird innerhalb eines Segments nicht mehr gemixt
BPM_HARD = 8.0             # darueber ist kein Uebergang mehr sinnvoll
BPM_PENALTY_PER_BPM = 1.0  # Zuschlag pro BPM ausserhalb der Toleranz
MAX_TRANSITION_COST = 3.0  # Gesamtkosten, ab denen geschnitten wird (Segmentgrenze)

CSV_COLUMNS = ["pos", "artist", "title", "bpm", "bpm_confidence", "bpm_octave",
               "camelot", "key_name", "key_confidence", "key_margin",
               "duration_s", "score", "path"]


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


def camelot_relation(k1: str | None, k2: str | None) -> str:
    """Beziehung zweier Camelot-Keys als Schluessel aus TRANSITION_COST.

    Der Radueberlauf zaehlt mit: 12A und 1A sind Nachbarn.
    """
    p1, p2 = parse_camelot(k1), parse_camelot(k2)
    if not p1 or not p2:
        return "unbekannt"
    if p1 == p2:
        return "gleich"
    n1, l1 = p1
    n2, l2 = p2
    if l1 == l2:
        diff = (n2 - n1) % 12
        if diff in (1, 11):
            return "rad_pm1"
        if diff == 2:
            return "boost2"
        if diff in (3, 9):
            return "boost3"
    if n1 == n2 and l1 != l2:
        return "relativ"
    return "fern"


# Alte Bezeichnung; wird von _eval/dj_export.py weiterverwendet.
harmonic_relation = camelot_relation


def transition_cost(t1: "Entry", t2: "Entry", bpm_tolerance: float = BPM_TOLERANCE,
                    max_bpm_step: float = BPM_COMFORT) -> tuple[float, str]:
    """Kosten eines Uebergangs t1 -> t2 (0.0 bis inf) und seine Einstufung.

    Ueber `max_bpm_step` ist der Uebergang kein Mixpunkt mehr, sondern eine
    Segmentgrenze: die Kosten sind dann `inf`. Der BPM-Anteil darunter waechst
    linear mit dem Abstand und wird NICHT gedeckelt - ein Deckel macht einen
    5-BPM-Sprung so billig wie einen 4-BPM-Sprung (genau dieser Fehler ist im
    Werkzeug einmal aufgefallen).
    """
    rel = camelot_relation(t1.camelot, t2.camelot)
    cost = TRANSITION_COST[rel]
    if t1.bpm is not None and t2.bpm is not None:
        dbpm = abs(t1.bpm - t2.bpm)
        if dbpm > max_bpm_step:
            return float("inf"), f"{rel} ({RELATION_LABEL[rel]})"
        cost += max(0.0, dbpm - bpm_tolerance)
    else:
        cost += 1.0            # unbekanntes BPM ist ein Risiko, kein Ausschluss
    return cost, f"{rel} ({RELATION_LABEL[rel]})"


def _edge_cost(a: "Entry", b: "Entry", bpm_tolerance: float = BPM_TOLERANCE) -> float:
    """Nur die Kosten; `inf` bedeutet: kein direkter Uebergang."""
    return transition_cost(a, b, bpm_tolerance)[0]


# ------------------------------------------------------------------ Datenmodell
@dataclass
class Entry:
    path: Path
    artist: str = ""
    title: str = ""
    bpm: float | None = None
    bpm_confidence: float = 0.0
    bpm_octave: int = 1
    camelot: str | None = None
    key_name: str | None = None
    key_confidence: float = 0.0
    key_margin: float = 0.0
    duration_s: float = 0.0
    score: float = 0.0

    @property
    def label(self) -> str:
        return f"{self.artist} - {self.title}".strip(" -") or self.path.stem

    @property
    def reliable_bpm(self) -> bool:
        return self.bpm is not None and self.bpm_confidence >= 0.5

    @property
    def reliable_key(self) -> bool:
        return self.camelot is not None and self.key_margin >= 0.05

    @property
    def is_anchor(self) -> bool:
        """Guter Mix-Punkt: Tempo und Tonart beide verlaesslich erkannt."""
        return self.reliable_bpm and self.reliable_key

    def to_dict(self) -> dict:
        return {
            "path": str(self.path), "artist": self.artist, "title": self.title,
            "bpm": self.bpm, "bpm_confidence": round(self.bpm_confidence, 3),
            "bpm_octave": self.bpm_octave, "camelot": self.camelot,
            "key_name": self.key_name, "key_confidence": round(self.key_confidence, 3),
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


def read_tags(path: Path) -> tuple[str, str, float]:
    """Artist, Titel und Dauer aus den Datei-Tags (mutagen), sonst leer.

    Ohne mutagen oder ohne Tags bleibt es bei leeren Feldern; der Aufrufer faellt
    dann auf den Dateinamen zurueck.
    """
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(str(path), easy=True)
        if audio is None:
            return "", "", 0.0
        tags = audio.tags or {}

        def first(name: str) -> str:
            value = tags.get(name)
            if isinstance(value, list):
                value = value[0] if value else ""
            return str(value or "").strip()

        duration = 0.0
        info = getattr(audio, "info", None)
        if info is not None and getattr(info, "length", None):
            duration = float(info.length)
        return first("artist"), first("title"), duration
    except Exception as exc:  # noqa: BLE001 - Tags sind Komfort, nie ein Abbruchgrund
        log.debug("Tags von %s nicht lesbar: %s", path.name, exc)
        return "", "", 0.0


def analyze_folder(folder: Path, *, recursive: bool = True) -> list[Entry]:
    """Analysiert alle Audio-Dateien eines Ordners (ohne Duplikate).

    BPM und Tonart kommen aus sc_digger.analysis, Artist/Titel/Dauer aus den Tags.
    Das Modul wird erst hier geladen, damit `--help` keine Audio-Bibliotheken braucht.
    """
    from sc_digger import analysis

    files = sorted(
        p for p in (folder.rglob("*") if recursive else folder.glob("*"))
        if p.is_file() and p.suffix.lower() in AUDIO_EXT
    )
    entries: list[Entry] = []
    for i, p in enumerate(files, 1):
        try:
            r = analysis.analyze_track(p)
        except Exception as exc:  # noqa: BLE001 - eine kaputte Datei stoppt den Ordner nicht
            log.warning("Analyse fehlgeschlagen fuer %s: %s", p.name, exc)
            continue
        artist, title, duration = read_tags(p)
        if not title:
            artist, title = guess_artist_title(p.stem)
        entries.append(Entry(
            path=p, artist=artist, title=title,
            bpm=r.get("bpm"), camelot=r.get("key_camelot"), key_name=r.get("key_name"),
            duration_s=duration,
        ))
        log.info("[%d/%d] %s -> %s BPM (%s) %s", i, len(files), p.name,
                 entries[-1].bpm, entries[-1].camelot, entries[-1].key_name)
    return entries


# ------------------------------------------------------------------ Playlist
@dataclass
class Playlist:
    entries: list[Entry] = field(default_factory=list)
    transitions: list[dict] = field(default_factory=list)
    segments: int = 1


def build_chain(pool: list[Entry], *, start: Entry | None = None,
                bpm_tolerance: float = BPM_TOLERANCE,
                max_cost: float = MAX_TRANSITION_COST) -> list[Entry]:
    """Kette aus Uebergaengen, die alle hoechstens `max_cost` kosten.

    Es wird nur ein Uebergang genommen, der wirklich bequem ist (Default:
    gleicher/benachbarter Key plus kleiner BPM-Unterschied). Reicht es nicht mehr,
    endet die Kette - lieber eine ehrliche Zäsur als ein unharmonischer Sprung.
    """
    if not pool:
        return []
    start = start or pool[0]
    chain = [start]
    rest = [e for e in pool if e is not start]

    while rest:
        cur = chain[-1]
        viable = [(_edge_cost(cur, e, bpm_tolerance), e) for e in rest]
        viable = [(c, e) for c, e in viable if c <= max_cost]
        if not viable:
            break
        if len(viable) > 1:
            # Bei fast gleichem Preis den Uebergang waehlen, von dem aus es
            # anschliessend am besten weitergeht (verhindert Sackgassen).
            def lookahead(item: tuple[float, Entry]) -> float:
                c, e = item
                others = [x for x in rest if x is not e]
                follow = min((_edge_cost(e, x, bpm_tolerance) for x in others), default=0.0)
                return c + 0.3 * min(follow, max_cost)
            cost, nxt = min(viable, key=lookahead)
        else:
            cost, nxt = viable[0]
        chain.append(nxt)
        rest.remove(nxt)
    return chain


def _segment_entries(entries: list[Entry], *, start: Entry | None = None,
                     bpm_tolerance: float = BPM_TOLERANCE,
                     max_cost: float = MAX_TRANSITION_COST) -> list[list[Entry]]:
    """Harmonisch zusammenhaengende Ketten, nach fallendem Durchschnitts-BPM.

    Innerhalb eines Segments passt jeder Uebergang; wo das nicht mehr geht, beginnt
    ein neues Segment. Begonnen wird jeweils beim Track mit dem mittleren Tempo des
    Rests (nicht am Rand), damit die Kette nicht in einer Sackgasse startet.
    """
    remaining = list(entries)
    if not remaining:
        return []

    segments: list[list[Entry]] = []
    while remaining:
        known = [e for e in remaining if e.bpm is not None]
        if start is None and known:
            med = statistics.median([e.bpm for e in known])
            s = min(known, key=lambda e: abs(e.bpm - med))
        else:
            s = start if start in remaining else remaining[0]
        chain = build_chain(remaining, start=s, bpm_tolerance=bpm_tolerance,
                            max_cost=max_cost)
        if not chain:            # sollte nicht passieren, verhindert Endlosschleife
            chain = [remaining[0]]
        for e in chain:
            remaining.remove(e)
        segments.append(chain)
        start = None

    def avg_bpm(seg: list[Entry]) -> float:
        vals = [e.bpm for e in seg if e.bpm is not None]
        return statistics.fmean(vals) if vals else 0.0

    # Set startet druckvoll: das schnellste Segment kommt zuerst.
    segments.sort(key=avg_bpm, reverse=True)
    return segments


def build_segments(entries: list[Entry],
                   max_cost: float = MAX_TRANSITION_COST) -> list[list[Entry]]:
    """Unterteilt Tracks in harmonisch zusammenhängende Ketten/Segmente.

    Überschreitet ein Übergang die Kostengrenze `max_cost`, wird geschnitten statt
    ein unharmonischer Sprung versteckt. Gibt die Segmente in Set-Reihenfolge zurück.
    """
    return _segment_entries(entries, max_cost=max_cost)


def order_harmonic(entries: list[Entry], *, start: Entry | None = None,
                   bpm_tolerance: float = BPM_TOLERANCE,
                   max_cost: float = MAX_TRANSITION_COST) -> Playlist:
    """Harmonisch sortierte Playlist in Segmenten inklusive Uebergangsprotokoll.

    Wo der naechste bequeme Uebergang fehlt, beginnt ein neues Segment - das ist die
    Stelle, an der im Set ohnehin ein Schnitt liegt. Ein heimlicher BPM-Sprung mitten
    in der Liste waere fuer den DJ-Workflow schlimmer als eine sichtbare Segmentgrenze.
    """
    segments = _segment_entries(entries, start=start, bpm_tolerance=bpm_tolerance,
                                max_cost=max_cost)
    if not segments:
        return Playlist([], [], 0)

    seq: list[Entry] = []
    transitions: list[dict] = []
    for si, seg in enumerate(segments, 1):
        if seq:
            last, first = seq[-1], seg[0]
            dbpm_cut = (None if (last.bpm is None or first.bpm is None)
                        else round(first.bpm - last.bpm, 1))
            transitions.append({"typ": "schnitt", "segment": si,
                                "von": last.label, "nach": first.label,
                                "harmonisch": camelot_relation(last.camelot, first.camelot),
                                "kosten": None, "dbpm": dbpm_cut,
                                "warnung": "Segmentgrenze (kein direkter Uebergang)"})
        for a, b in zip(seg, seg[1:]):
            rel = camelot_relation(a.camelot, b.camelot)
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
                                "kosten": round(_edge_cost(a, b, bpm_tolerance), 2),
                                "dbpm": dbpm, "warnung": warn})
        seq.extend(seg)

    return Playlist(seq, transitions, len(segments))


# ------------------------------------------------------------------ Ausgabe
def _write_m3u8(pl: Playlist, out: Path, *, title: str | None = None,
                absolute: bool = False, mark_segments: bool = True) -> Path:
    lines = ["#EXTM3U"]
    if title:
        lines.append(f"#PLAYLIST:{title}")
    current_seg = None
    for e in pl.entries:
        seg = next((t["segment"] for t in pl.transitions
                    if t["typ"] == "uebergang" and t["nach"] == e.label), None)
        if mark_segments and seg is not None and seg != current_seg:
            current_seg = seg
            lines.append(f"# Segment {seg} (harmonisch fortlaufend)")
        secs = int(e.duration_s) if e.duration_s else -1
        info = f"{e.artist} - {e.title}" if e.artist else e.title
        tag = f"{e.camelot or '--'} · {e.bpm:.0f} BPM" if e.bpm else f"{e.camelot or '--'} · -- BPM"
        lines.append(f"#EXTINF:{secs},{info} [{tag}]")
        lines.append(str(e.path if absolute else e.path.name))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def write_m3u8(pl: Playlist, out: Path, *, absolute: bool = False,
               mark_segments: bool = True) -> Path:
    """Schreibt eine bereits sortierte Playlist (ohne Kopfzeile mit dem Set-Namen)."""
    return _write_m3u8(pl, out, absolute=absolute, mark_segments=mark_segments)


def export_m3u8(entries: list[Entry], path: Path, title: str = "DJ Set") -> Path:
    """Schreibt eine M3U8-Playlist mit Segmentmarken (absolute Pfade)."""
    pl = order_harmonic(list(entries))
    return _write_m3u8(pl, path, title=title, absolute=True)


def export_csv(entries: list[Entry], path: Path) -> Path:
    """Schreibt alle Track-Einträge als CSV (Komma-getrennt, wieder einlesbar)."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_COLUMNS)
        for i, e in enumerate(entries, 1):
            w.writerow([i, e.artist, e.title, e.bpm, e.bpm_confidence, e.bpm_octave,
                        e.camelot, e.key_name, e.key_confidence, e.key_margin,
                        e.duration_s, e.score, str(e.path)])
    return path


def write_csv(pl: Playlist, out: Path) -> Path:
    """Schreibt die Playlist als CSV mit Semikolon (Excel-Format, aeltere Aufrufe).

    Enthaelt gegenueber export_csv zwei zusaetzliche Spalten (reliable_bpm/key).
    """
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
    """Pruefbericht: Verteilung, Problemzonen und Qualitaet der Uebergaenge."""
    n = len(entries)
    with_bpm = [e for e in entries if e.bpm is not None]
    with_key = [e for e in entries if e.camelot]
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
    pl_key = [e for e in pl.entries if e.camelot]
    if pl_bpm:
        lines += ["", f"BPM-Bereich (Playlist)    : {min(pl_bpm):.0f} - {max(pl_bpm):.0f}",
                  f"BPM-Median (Playlist)     : {statistics.median(pl_bpm):.0f}"]
    if pl_key:
        kc: dict[str, int] = {}
        for e in pl_key:
            kc[e.camelot] = kc.get(e.camelot, 0) + 1
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
                grund.append(f"Key {e.camelot or '--'} (Marge {e.key_margin:.3f})")
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


def export_report(entries: list[Entry], path: Path,
                  *, bpm_tolerance: float = BPM_TOLERANCE) -> Path:
    """Erzeugt den Set-Report zu einer Track-Menge (sortiert sie dabei harmonisch)."""
    pl = order_harmonic(list(entries), bpm_tolerance=bpm_tolerance)
    return write_report(list(entries), pl, path, bpm_tolerance=bpm_tolerance)


def _read_entries(path: Path, delimiter: str) -> list[Entry]:
    out: list[Entry] = []
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter=delimiter):
            out.append(Entry(
                path=Path(row["path"]), artist=row["artist"], title=row["title"],
                bpm=float(row["bpm"]) if row["bpm"] else None,
                bpm_confidence=float(row.get("bpm_confidence") or 0),
                bpm_octave=int(float(row.get("bpm_octave") or 1)),
                camelot=row["camelot"] or None, key_name=row["key_name"] or None,
                key_confidence=float(row.get("key_confidence") or 0),
                key_margin=float(row.get("key_margin") or 0),
                duration_s=float(row["duration_s"]) if row["duration_s"] else 0.0,
                score=float(row["score"]) if row.get("score") else 0.0,
            ))
    return out


def load_entries(path: Path) -> list[Entry]:
    """Liest eine von write_csv geschriebene CSV (Semikolon) wieder ein."""
    return _read_entries(path, ";")


# ------------------------------------------------------------------ Hauptfunktion
def build_dj_set(
    folder_or_csv: Path,
    out_dir: Path,
    name: str = "set",
    bpm_range: tuple[float, float] | None = None,
    from_csv: bool = False,
) -> tuple[Path, Path, Path]:
    """Baut M3U8, CSV und Report aus Ordner oder CSV. Gibt die drei Pfade zurueck.

    `from_csv=True` (oder ein Pfad auf eine .csv) ueberspringt die Audio-Analyse und
    liest die Metadaten aus einer zuvor erzeugten CSV - so bleibt das Set nach einer
    Filterung ohne erneutes Analysieren neu sortierbar.
    """
    src = Path(folder_or_csv)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    use_csv = from_csv or (src.is_file() and src.suffix.lower() == ".csv")
    if use_csv:
        if not src.is_file():
            raise FileNotFoundError(f"--from-csv erwartet eine CSV-Datei: {src}")
        entries = _read_entries(src, ",")
        log.info("%d Eintraege aus %s geladen (keine Neuanalyse)", len(entries), src.name)
    else:
        if not src.is_dir():
            raise NotADirectoryError(f"Ordner nicht gefunden: {src}")
        entries = analyze_folder(src)

    candidates = list(entries)
    if bpm_range:
        lo, hi = bpm_range
        candidates = [e for e in candidates if e.bpm is not None and lo <= e.bpm <= hi]

    pl = order_harmonic(candidates)
    m3u8_path = _write_m3u8(pl, out_dir / f"{name}.m3u8", title=name, absolute=True)
    csv_path = export_csv(pl.entries, out_dir / f"{name}.csv")
    report_path = write_report(candidates, pl, out_dir / f"{name}_report.txt")
    return m3u8_path, csv_path, report_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m sc_digger.dj_export",
        description="Harmonische Playlist (M3U8), CSV und Report aus analysierten Tracks",
        epilog="Beispiel: python -m sc_digger.dj_export --folder /music/inbox --out ./sets "
               "--name peaktime --bpm 150 165")
    ap.add_argument("--folder", required=True,
                    help="Ordner mit Audio-Dateien - ODER eine zuvor erzeugte CSV "
                         "(dann wird nicht neu analysiert)")
    ap.add_argument("--out", default=".", help="Zielordner fuer Playlist/CSV/Report")
    ap.add_argument("--name", default="set", help="Basisname der Ausgabedateien")
    ap.add_argument("--bpm", nargs=2, type=float, metavar=("MIN", "MAX"),
                    help="BPM-Fenster; nur Tracks darin kommen in die Playlist")
    ap.add_argument("--from-csv", action="store_true",
                    help="erzwingt CSV statt Analyse (wird bei *.csv automatisch erkannt)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    m3u, out, report = build_dj_set(
        Path(a.folder), Path(a.out), name=a.name,
        bpm_range=(a.bpm[0], a.bpm[1]) if a.bpm else None, from_csv=a.from_csv)
    print(f"Playlist : {m3u}\nCSV      : {out}\nReport   : {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
