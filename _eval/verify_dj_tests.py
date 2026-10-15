"""Ersatzlauf fuer die tmp_path-Tests, die in der Sandbox an pytest scheitern.

Fuehrt dieselben Zusicherungen gegen einen echten Arbeitsordner aus. Alle Pfade
werden aus __file__ abgeleitet, damit das Skript unabhaengig vom aktuellen
Arbeitsverzeichnis laeuft.

Aufruf:  python _eval/verify_dj_tests.py   (aus dem Repo-Wurzelverzeichnis)
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import numpy as np

EVAL = Path(__file__).resolve().parent          # .../<repo>/_eval
REPO = EVAL.parent                              # .../<repo>
sys.path.insert(0, str(EVAL))                   # analysis_v2, dj_export

import analysis_v2 as A          # noqa: E402
import dj_export as D            # noqa: E402


def _load_test_helpers():
    """Laedt _entry/_written_pad aus tests/test_dj_workflow.py, ohne Paketpfad."""
    spec = importlib.util.spec_from_file_location(
        "_dj_helpers", REPO / "tests" / "test_dj_workflow.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_HELPERS = _load_test_helpers()
_entry = _HELPERS._entry
_written_pad = _HELPERS._written_pad

WORK = EVAL / "_tmp_verify"
failures: list[str] = []


def check(name: str, cond: bool, detail: object = "") -> None:
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))
    if not cond:
        failures.append(name)


def test_end_to_end_analysis_to_playlist() -> None:
    print("test_end_to_end_analysis_to_playlist")
    d = WORK / "e2e"
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    cases = [("a", 0, 0, 155.0, "5A"), ("b", 5, 0, 155.0, "4A"), ("c", 10, 0, 156.0, "3A")]
    entries = []
    for name, root, mode, bpm, want_key in cases:
        p = _written_pad(d / f"{name}.wav", root, mode, bpm=bpm)
        r = A.analyze(p)
        check(f"{name}: BPM erkannt", r["bpm"] is not None, r)
        check(f"{name}: BPM {bpm} +-2", r["bpm"] is not None and abs(r["bpm"] - bpm) <= 2.0, r["bpm"])
        check(f"{name}: Key {want_key}", r["key_camelot"] == want_key, r["key_camelot"])
        cands = r["key_candidates"]
        entries.append(D.Entry(path=p, artist="T", title=name, bpm=r["bpm"],
                               bpm_confidence=r["bpm_confidence"],
                               key=r["key_camelot"], key_name=r["key_name"],
                               key_margin=cands[0]["r"] - cands[1]["r"]))
    pl = D.order_harmonic(entries)
    check("3 Tracks in Playlist", len(pl.entries) == 3, len(pl.entries))
    inner = [t for t in pl.transitions if t["typ"] == "uebergang"]
    check("2 echte Uebergaenge", len(inner) == 2, pl.transitions)
    check("alle Uebergaenge harmonisch nah",
          all(t["harmonisch"] in ("gleich", "rad_pm1", "relativ") for t in inner), inner)
    check("alle Uebergaenge im BPM-Komfort",
          all(abs(t["dbpm"]) <= D.BPM_COMFORT for t in inner), inner)
    m3u = D.write_m3u8(pl, d / "set.m3u8")
    check("Playlist geschrieben", m3u.exists() and m3u.stat().st_size > 0)


def test_write_outputs_and_reload() -> None:
    print("test_write_outputs_and_reload")
    d = WORK / "export"
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    entries = [_entry(f"t{i}", 152 + i, ["5A", "6A", "7A"][i % 3]) for i in range(6)]
    for e in entries:
        e.path = d / f"{e.title}.wav"
        e.path.write_bytes(b"x")
        e.duration_s = 300.0
    pl = D.order_harmonic(entries)
    m3u = D.write_m3u8(pl, d / "set.m3u8")
    csv_path = D.write_csv(pl, d / "set.csv")
    rep = D.write_report(entries, pl, d / "set_report.txt")

    text = m3u.read_text(encoding="utf-8")
    check("M3U8 beginnt mit #EXTM3U", text.startswith("#EXTM3U"))
    check("6 Eintraege", text.count("#EXTINF") == 6, text.count("#EXTINF"))
    check("BPM im Tag", "BPM" in text)
    check("Segmentmarke vorhanden", "# Segment 1" in text)

    reloaded = D.load_entries(csv_path)
    check("CSV 6 Zeilen", len(reloaded) == 6, len(reloaded))
    check("BPM roundtrip", [e.bpm for e in reloaded] == [round(e.bpm, 1) for e in pl.entries])
    check("Key roundtrip", [e.key for e in reloaded] == [e.key for e in pl.entries])

    rtext = rep.read_text(encoding="utf-8")
    check("Report Kopf", "Tracks insgesamt" in rtext)
    check("Report Verteilung", "Harmonische Verteilung" in rtext)
    check("Report Segmente", "Segmente" in rtext)


def test_end_to_end_writes_playlist() -> None:
    print("test_end_to_end_writes_playlist")
    d = WORK / "e2e_write"
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    entries = []
    for name, bpm, key, root in (("a", 155.0, "5A", 0), ("b", 156.0, "4A", 5)):
        p = _written_pad(d / f"{name}.wav", root, 0, bpm=bpm, seconds=20.0)
        entries.append(D.Entry(path=p, artist="T", title=name, bpm=bpm,
                               bpm_confidence=0.9, key=key, key_margin=0.2))
    pl = D.order_harmonic(entries)
    out = D.write_m3u8(pl, d / "set.m3u8")
    check("M3U8 geschrieben und beginnt korrekt",
          out.read_text(encoding="utf-8").startswith("#EXTM3U"))


def test_resample_preserves_pitch_class() -> None:
    print("test_resample_preserves_pitch_class")
    t = np.arange(A.SR * 5) / A.SR
    tone = (0.5 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    for target in (A.SR_CHROMA, 22050):
        ys = A.resample(tone, A.SR, target)
        ch, w = A.chroma(ys, sr=target)
        m = np.average(ch, axis=1, weights=w)
        check(f"440 Hz bleibt A nach Resampling auf {target}", int(np.argmax(m)) == 9,
              int(np.argmax(m)))


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    test_write_outputs_and_reload()
    test_end_to_end_analysis_to_playlist()
    test_end_to_end_writes_playlist()
    test_resample_preserves_pitch_class()
    print(f"\n{'ALLE PRUEFUNGEN OK' if not failures else 'FEHLGESCHLAGEN: ' + ', '.join(failures)}")
    shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
