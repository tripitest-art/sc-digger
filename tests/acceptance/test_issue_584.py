"""Akzeptanztests: Prozentangaben im Track-Explorer eindeutig beschriften."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRACKS = (ROOT / "sc_digger" / "web" / "templates" / "tracks.html").read_text(encoding="utf-8")


def test_breakdown_label_nennt_den_anteil():
    assert "Max. Breakdown-Anteil (0.15 = 15 %)" in TRACKS


def test_taste_label_nennt_den_anteil():
    assert "Min. Taste-Anteil (0.7 = 70 %)" in TRACKS


def test_irrefuehrende_labels_sind_weg():
    assert "Max. Breakdown %" not in TRACKS
    assert ">Min. Taste<" not in TRACKS


def test_platzhalter_passt_zur_einheit():
    assert 'placeholder="z. B. 0.15"' in TRACKS
    assert 'placeholder="z. B. 0.7"' in TRACKS
