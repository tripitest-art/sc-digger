"""Tests fuer sc_digger.dj_export: Kantenkosten, Segmentbildung, Dateiexporte.

Alles laeuft ohne Netz und ohne Audio-Analyse: die Eintraege werden direkt gebaut,
die Dateien landen in tmp_path. Nur der Report zaehlt Verlaesslichkeiten, dafuer
werden Konfidenz und Marge von Hand gesetzt.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from sc_digger.dj_export import (
    BPM_COMFORT,
    BPM_TOLERANCE,
    CSV_COLUMNS,
    Entry,
    TRANSITION_COST,
    build_dj_set,
    build_segments,
    camelot_relation,
    export_csv,
    export_m3u8,
    export_report,
    load_entries,
    main,
    order_harmonic,
    parse_camelot,
    transition_cost,
    write_csv,
)


def _entry(name: str, bpm: float | None, camelot: str | None,
           margin: float = 0.2, confidence: float = 0.9) -> Entry:
    return Entry(path=Path(f"/music/{name}.wav"), artist="A", title=name, bpm=bpm,
                 bpm_confidence=confidence, camelot=camelot, key_name="Cm",
                 key_confidence=confidence, key_margin=margin, duration_s=300.0)


# ------------------------------------------------------------------ Camelot
@pytest.mark.parametrize("key, expect", [
    ("5A", (5, "A")),
    ("12B", (12, "B")),
    ("12b", (12, "B")),
    ("invalid", None),
    ("5C", None),
    ("0A", None),
    ("13A", None),
    ("", None),
    (None, None),
])
def test_parse_camelot(key, expect):
    assert parse_camelot(key) == expect


@pytest.mark.parametrize("a, b, expect", [
    ("5A", "5A", "gleich"),
    ("5A", "6A", "rad_pm1"),
    ("5A", "4A", "rad_pm1"),
    ("12A", "1A", "rad_pm1"),      # Radueberlauf
    ("1A", "12A", "rad_pm1"),
    ("5A", "5B", "relativ"),
    ("8A", "8B", "relativ"),       # relative Dur ist kein Energie-Boost
    ("5A", "7A", "boost2"),
    ("5A", "8A", "boost3"),
    ("5A", "2A", "boost3"),        # -3 auf dem Rad
    ("5A", "11A", "fern"),
    ("5A", "7B", "fern"),          # anderer Buchstabe und 2 Schritte
    ("5A", None, "unbekannt"),
    (None, None, "unbekannt"),
])
def test_camelot_relation(a, b, expect):
    assert camelot_relation(a, b) == expect


# ------------------------------------------------------------------ Kantenkosten
def test_cost_same_track_is_zero_and_labeled():
    cost, label = transition_cost(_entry("a", 155.0, "5A"), _entry("b", 155.0, "5A"))
    assert cost == 0.0
    assert "gleich" in label


def test_cost_uses_harmonic_distance():
    a = _entry("a", 155.0, "5A")
    assert transition_cost(a, _entry("b", 155.0, "6A"))[0] == TRANSITION_COST["rad_pm1"]
    assert transition_cost(a, _entry("c", 155.0, "5B"))[0] == TRANSITION_COST["relativ"]
    assert transition_cost(a, _entry("d", 155.0, "7A"))[0] == TRANSITION_COST["boost2"]
    assert transition_cost(a, _entry("e", 155.0, "11A"))[0] == TRANSITION_COST["fern"]


def test_cost_is_monotonic_across_rad_neighbours():
    a = _entry("a", 155.0, "5A")
    same = transition_cost(a, _entry("b", 155.0, "5A"))[0]
    neighbour = transition_cost(a, _entry("c", 155.0, "6A"))[0]
    relative = transition_cost(a, _entry("d", 155.0, "5B"))[0]
    far = transition_cost(a, _entry("e", 155.0, "11A"))[0]
    assert same < neighbour < relative < far


def test_bpm_penalty_starts_after_tolerance_and_grows_linearly():
    a = _entry("a", 155.0, "5A")
    assert transition_cost(a, _entry("b", 155.0 + BPM_TOLERANCE, "5A"))[0] == 0.0
    # Innerhalb der Komfortgrenze gibt es nur einen Zuschlag bis 1.0; die
    # Linearitaet zeigt sich, wenn man die Grenze bewusst weiter aufzieht.
    assert transition_cost(a, _entry("c", 159.0, "5A"), max_bpm_step=10.0)[0] == 1.0
    assert transition_cost(a, _entry("d", 160.0, "5A"), max_bpm_step=10.0)[0] == 2.0
    assert transition_cost(a, _entry("e", 162.0, "5A"), max_bpm_step=10.0)[0] == 4.0


def test_cost_is_infinite_just_beyond_the_comfort_window():
    """Kein Deckel auf dem BPM-Zuschlag: 4+ BPM ist eine Segmentgrenze, kein Mix."""
    a = _entry("a", 155.0, "5A")
    assert transition_cost(a, _entry("b", 155.0 + BPM_COMFORT, "5A"))[0] < float("inf")
    for jump in (BPM_COMFORT + 0.5, 5.0, 15.0):
        cost, _ = transition_cost(a, _entry("c", 155.0 + jump, "5A"))
        assert cost == float("inf"), jump


def test_smaller_comfort_window_can_be_requested():
    a = _entry("a", 155.0, "5A")
    assert transition_cost(a, _entry("b", 158.0, "5A"), max_bpm_step=1.0)[0] == float("inf")
    assert transition_cost(a, _entry("c", 158.0, "5A"), max_bpm_step=3.0)[0] == 0.0


def test_unknown_bpm_is_penalised_but_not_forbidden():
    cost, label = transition_cost(_entry("a", 155.0, "5A"), _entry("b", None, "5A"))
    assert 0.0 < cost < TRANSITION_COST["fern"]
    assert "gleich" in label
    # Auch beidseitig unbekanntes Tempo bleibt ein Risiko-Zuschlag, kein Freifahrtschein.
    cost2, label2 = transition_cost(_entry("c", None, "5A"), _entry("d", None, "5A"))
    assert cost2 == 1.0
    assert label2.startswith("gleich")


def test_unknown_key_is_expensive_but_computable():
    cost, label = transition_cost(_entry("a", 155.0, None), _entry("b", 155.0, None))
    assert cost == TRANSITION_COST["unbekannt"]
    assert "unbekannt" in label


# ------------------------------------------------------------------ Segmente
def test_segments_keep_every_track_exactly_once():
    entries = [_entry(f"t{i}", 152.0 + i, ["5A", "6A", "7A"][i % 3]) for i in range(9)]
    segments = build_segments(entries)
    flat = [e for seg in segments for e in seg]
    assert len(flat) == len(entries)
    assert {id(e) for e in flat} == {id(e) for e in entries}


def test_segments_split_bpm_worlds_instead_of_jumping():
    nah = [_entry(f"s{i}", 150.0 + i * 0.5, "5A") for i in range(4)]
    fern = [_entry(f"f{i}", 170.0 + i * 0.5, "5A") for i in range(4)]
    segments = build_segments(nah + fern, max_cost=3.0)
    assert len(segments) == 2
    assert all(len(seg) == 4 for seg in segments)
    for seg in segments:
        for a, b in zip(seg, seg[1:]):
            assert abs(a.bpm - b.bpm) <= BPM_COMFORT


def test_segments_stay_one_chain_when_everything_fits():
    entries = [_entry(f"t{i}", 152.0 + i, "5A") for i in range(6)]
    segments = build_segments(entries)
    assert len(segments) == 1
    assert len(segments[0]) == 6


def test_segments_start_with_the_fastest_block():
    """Ein Set startet druckvoll: erstes Segment hat das hoechste Durchschnitts-BPM."""
    entries = ([_entry(f"s{i}", 150.0 + i, "5A") for i in range(3)]
               + [_entry(f"f{i}", 168.0 + i, "5A") for i in range(3)])
    segments = build_segments(entries)
    assert len(segments) == 2
    assert min(e.bpm for e in segments[0]) >= 168.0


def test_harmonic_neighbours_are_preferred_over_far_keys():
    entries = [_entry(f"k{i}", 155.0, key)
               for i, key in enumerate(["5A", "5B", "6A", "7A", "11A"])]
    segments = build_segments(entries)
    assert len(segments[0]) > 1
    for a, b in zip(segments[0], segments[0][1:]):
        assert camelot_relation(a.camelot, b.camelot) in ("gleich", "rad_pm1", "relativ")


def test_segments_handle_empty_and_single_input():
    assert build_segments([]) == []
    one = [_entry("only", 155.0, "5A")]
    assert build_segments(one) == [one]


def test_segments_without_bpm_still_terminate():
    entries = [_entry(f"x{i}", None, "5A") for i in range(3)]
    segments = build_segments(entries)
    assert len([e for seg in segments for e in seg]) == 3


# ------------------------------------------------------------------ Sortierung
def test_order_harmonic_marks_the_cut_between_two_bpm_worlds():
    entries = ([_entry(f"s{i}", 150.0 + i, "5A") for i in range(3)]
               + [_entry(f"f{i}", 170.0 + i, "5A") for i in range(3)])
    pl = order_harmonic(entries)
    assert pl.segments == 2
    cuts = [t for t in pl.transitions if t["typ"] == "schnitt"]
    assert len(cuts) == 1
    assert cuts[0]["warnung"].startswith("Segmentgrenze")
    for t in pl.transitions:
        if t["typ"] == "uebergang":
            assert abs(t["dbpm"]) <= BPM_COMFORT, t


def test_order_harmonic_is_a_permutation_of_the_input():
    entries = [_entry(f"t{i}", 150.0 + i, ["5A", "6A", "7A", "8A"][i % 4]) for i in range(8)]
    pl = order_harmonic(entries)
    assert {id(e) for e in pl.entries} == {id(e) for e in entries}


def test_order_harmonic_empty_and_single():
    empty = order_harmonic([])
    assert (empty.entries, empty.transitions, empty.segments) == ([], [], 0)
    one = [_entry("only", 155.0, "5A")]
    pl = order_harmonic(one)
    assert pl.entries == one and pl.transitions == [] and pl.segments == 1


# ------------------------------------------------------------------ Entry-Verhalten
def test_entry_reliability_and_anchor():
    assert _entry("g", 155.0, "5A", margin=0.2, confidence=0.9).is_anchor
    assert not _entry("weak_key", 155.0, "5A", margin=0.005).is_anchor
    assert not _entry("weak_bpm", 155.0, "5A", confidence=0.2).is_anchor
    assert not _entry("no_key", 155.0, None).reliable_key


def test_entry_label_and_to_dict():
    e = _entry("t1", 155.0, "5A")
    assert e.label == "A - t1"
    empty = Entry(path=Path("/music/ohne tags.wav"))
    assert empty.label == "ohne tags"
    d = e.to_dict()
    assert d["camelot"] == "5A"
    assert d["reliable_bpm"] is True and d["reliable_key"] is True
    assert d["path"] == "/music/t1.wav"


# ------------------------------------------------------------------ Exporte
def test_export_m3u8_writes_segments_and_keys(tmp_path):
    entries = [_entry(f"t{i}", 155.0, ["5A", "6A"][i % 2]) for i in range(2)]
    for e in entries:
        e.path = tmp_path / e.path.name
    out = export_m3u8(entries, tmp_path / "set.m3u8", title="Mein Set")
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert text.startswith("#EXTM3U")
    assert "#PLAYLIST:Mein Set" in text
    assert text.count("#EXTINF") == 2
    assert "A - t0" in text and "5A · 155 BPM" in text
    assert str(tmp_path / "t0.wav") in text          # absolute Pfade fuer DJ-Software


def test_export_m3u8_marks_segment_boundaries(tmp_path):
    entries = ([_entry(f"s{i}", 150.0 + i, "5A") for i in range(2)]
               + [_entry(f"f{i}", 170.0 + i, "5A") for i in range(2)])
    out = export_m3u8(entries, tmp_path / "set.m3u8")
    text = out.read_text(encoding="utf-8")
    assert "# Segment 1" in text and "# Segment 2" in text


def test_export_csv_has_the_documented_columns(tmp_path):
    t1 = Entry(path=tmp_path / "t1.wav", artist="Artist A", title="Track 1",
               bpm=155.0, camelot="5A", duration_s=180.0)
    t2 = Entry(path=tmp_path / "t2.wav", artist="Artist B", title="Track 2",
               bpm=156.0, camelot="6A", duration_s=210.0)
    out = export_csv([t1, t2], tmp_path / "set.csv")
    with out.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == CSV_COLUMNS
    assert len(rows) == 2
    assert rows[0]["camelot"] == "5A" and rows[0]["artist"] == "Artist A"
    assert rows[0]["bpm"] == "155.0" and rows[1]["bpm"] == "156.0"
    assert rows[1]["path"] == str(tmp_path / "t2.wav")


def test_export_csv_round_trip_through_write_and_load(tmp_path):
    entries = [_entry(f"t{i}", 152.0 + i, ["5A", "6A", "7A"][i % 3]) for i in range(6)]
    for e in entries:
        e.path = tmp_path / f"{e.title}.wav"
    pl = order_harmonic(entries)
    csv_path = write_csv(pl, tmp_path / "set.csv")
    reloaded = load_entries(csv_path)
    assert [e.bpm for e in reloaded] == [e.bpm for e in pl.entries]
    assert [e.camelot for e in reloaded] == [e.camelot for e in pl.entries]
    assert [e.artist for e in reloaded] == [e.artist for e in pl.entries]


def test_export_report_lists_counts_and_playlist(tmp_path):
    entries = [_entry(f"t{i}", 152.0 + i, "5A") for i in range(3)]
    out = export_report(entries, tmp_path / "report.txt")
    text = out.read_text(encoding="utf-8")
    assert "Tracks insgesamt" in text and ": 3" in text
    assert "Mix-Anker (BPM+Key sicher): 3" in text
    assert "Harmonische Verteilung" in text
    assert "Uebergaenge" in text


def test_export_report_flags_uncertain_tracks(tmp_path):
    sicher = _entry("sicher", 155.0, "5A")
    unsicher = _entry("unsicher", 155.0, "5A", margin=0.001, confidence=0.1)
    out = export_report([sicher, unsicher], tmp_path / "report.txt")
    text = out.read_text(encoding="utf-8")
    assert "Unsicher / manuell pruefen" in text
    assert "unsicher" in text


# ------------------------------------------------------------------ build_dj_set
def _write_csv(path: Path, rows: list[list]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_COLUMNS)
        for row in rows:
            w.writerow(row)
    return path


def _csv_row(pos, artist, title, bpm, camelot, path):
    return [pos, artist, title, bpm, 0.9, 1, camelot, "Cm", 0.9, 0.2, 180, 80, path]


def test_build_dj_set_from_csv_writes_all_three_files(tmp_path):
    src = _write_csv(tmp_path / "input.csv", [
        _csv_row(1, "Art1", "Tit1", 155.0, "5A", str(tmp_path / "1.wav")),
        _csv_row(2, "Art2", "Tit2", 156.0, "6A", str(tmp_path / "2.wav")),
    ])
    m3u8, csv_out, report = build_dj_set(src, tmp_path / "out", name="my_set",
                                         from_csv=True)
    assert (m3u8.name, csv_out.name, report.name) == (
        "my_set.m3u8", "my_set.csv", "my_set_report.txt")
    for p in (m3u8, csv_out, report):
        assert p.parent == tmp_path / "out" and p.stat().st_size > 0
    assert "#EXTM3U" in m3u8.read_text(encoding="utf-8")
    assert "Tit1" in m3u8.read_text(encoding="utf-8")


def test_build_dj_set_detects_csv_by_suffix(tmp_path):
    src = _write_csv(tmp_path / "input.csv",
                     [_csv_row(1, "Art1", "Tit1", 155.0, "5A", "1.wav")])
    m3u8, csv_out, report = build_dj_set(src, tmp_path / "out", name="auto")
    assert all(p.exists() for p in (m3u8, csv_out, report))


def test_build_dj_set_bpm_range_filters_the_playlist(tmp_path):
    src = _write_csv(tmp_path / "input.csv", [
        _csv_row(1, "Langsam", "Tit1", 145.0, "5A", "1.wav"),
        _csv_row(2, "Schnell", "Tit2", 158.0, "5A", "2.wav"),
    ])
    m3u8, csv_out, _ = build_dj_set(src, tmp_path / "out", name="gefiltert",
                                    bpm_range=(150.0, 165.0), from_csv=True)
    text = m3u8.read_text(encoding="utf-8")
    assert "Schnell" in text and "Langsam" not in text
    with csv_out.open(newline="", encoding="utf-8") as fh:
        assert len(list(csv.DictReader(fh))) == 1


def test_build_dj_set_from_csv_requires_a_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_dj_set(tmp_path / "gibt-es-nicht.csv", tmp_path / "out", from_csv=True)


def test_build_dj_set_requires_a_folder(tmp_path):
    with pytest.raises(NotADirectoryError):
        build_dj_set(tmp_path / "kein-ordner", tmp_path / "out")


# ------------------------------------------------------------------ CLI
def test_cli_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    ausgabe = capsys.readouterr().out
    assert "--folder" in ausgabe and "--from-csv" in ausgabe


def test_cli_builds_a_set_from_csv(tmp_path, capsys):
    src = _write_csv(tmp_path / "input.csv", [
        _csv_row(1, "Art1", "Tit1", 155.0, "5A", "1.wav"),
        _csv_row(2, "Art2", "Tit2", 156.0, "6A", "2.wav"),
    ])
    assert main(["--folder", str(src), "--out", str(tmp_path / "out"),
                 "--name", "cli", "--from-csv"]) == 0
    assert "cli.m3u8" in capsys.readouterr().out
    assert (tmp_path / "out" / "cli.csv").exists()
