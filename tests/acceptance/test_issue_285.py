import csv
from pathlib import Path
import pytest
from sc_digger.dj_export import (
    Entry,
    build_dj_set,
    build_segments,
    camelot_relation,
    export_csv,
    export_m3u8,
    parse_camelot,
    transition_cost,
)


def test_parse_camelot_and_relations():
    assert parse_camelot("5A") == (5, "A")
    assert parse_camelot("12B") == (12, "B")
    assert parse_camelot("invalid") is None

    assert camelot_relation("5A", "5A") == "gleich"
    assert camelot_relation("5A", "6A") == "rad_pm1"
    assert camelot_relation("5A", "4A") == "rad_pm1"
    assert camelot_relation("12A", "1A") == "rad_pm1"  # Rad-Überlauf
    assert camelot_relation("5A", "5B") == "relativ"
    assert camelot_relation("5A", "7A") == "boost2"
    assert camelot_relation("5A", "11A") == "fern"


def test_transition_cost_and_segments():
    t1 = Entry(path=Path("t1.wav"), bpm=155.0, camelot="5A")
    t2 = Entry(path=Path("t2.wav"), bpm=156.0, camelot="6A")
    t3_unharmonisch = Entry(path=Path("t3.wav"), bpm=165.0, camelot="11A")

    cost12, label12 = transition_cost(t1, t2)
    assert cost12 <= 3.0
    assert "rad_pm1" in label12

    cost23, _ = transition_cost(t2, t3_unharmonisch)
    assert cost23 > 3.0

    segments = build_segments([t1, t2, t3_unharmonisch], max_cost=3.0)
    assert len(segments) >= 2


def test_export_m3u8_and_csv(tmp_path):
    t1 = Entry(path=tmp_path / "t1.wav", artist="Artist A", title="Track 1", bpm=155.0, camelot="5A", duration_s=180.0)
    t2 = Entry(path=tmp_path / "t2.wav", artist="Artist B", title="Track 2", bpm=156.0, camelot="6A", duration_s=210.0)

    m3u8_file = export_m3u8([t1, t2], tmp_path / "set.m3u8", title="Test Set")
    assert m3u8_file.exists()
    content = m3u8_file.read_text(encoding="utf-8")
    assert "#EXTM3U" in content
    assert "Track 1" in content

    csv_file = export_csv([t1, t2], tmp_path / "set.csv")
    assert csv_file.exists()
    with open(csv_file, newline="", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 2
        assert reader[0]["camelot"] == "5A"
        assert reader[1]["bpm"] == "156.0"


def test_build_dj_set_from_csv(tmp_path):
    csv_in = tmp_path / "input.csv"
    with open(csv_in, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["pos", "artist", "title", "bpm", "bpm_confidence", "bpm_octave",
                         "camelot", "key_name", "key_confidence", "key_margin",
                         "duration_s", "score", "path"])
        writer.writerow([1, "Art1", "Tit1", 155.0, 0.9, 1, "5A", "Cm", 0.9, 0.2, 180, 80, str(tmp_path / "1.wav")])
        writer.writerow([2, "Art2", "Tit2", 156.0, 0.9, 1, "6A", "Gm", 0.9, 0.2, 190, 85, str(tmp_path / "2.wav")])

    out_m3u8, out_csv, out_rep = build_dj_set(csv_in, tmp_path / "out", name="my_set", from_csv=True)
    assert out_m3u8.exists()
    assert out_csv.exists()
    assert out_rep.exists()
