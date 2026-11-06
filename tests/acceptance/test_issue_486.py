"""Akzeptanztests: Kontrast der Textfarben und Tastaturfokus im Dashboard."""
import re
from pathlib import Path

import pytest

BASE = (Path(__file__).resolve().parents[2] / "sc_digger" / "web" / "templates" / "base.html").read_text(encoding="utf-8")


def _var(name):
    m = re.search(r"--" + re.escape(name) + r":\s*(#[0-9a-fA-F]{6})\b", BASE)
    assert m, name
    return m.group(1)


def _lum(hexfarbe):
    kanaele = [int(hexfarbe[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in kanaele]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _kontrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("vordergrund", ["text-main", "text-muted", "text-dim", "accent-ok", "accent-warn", "accent-err", "accent-primary"])
@pytest.mark.parametrize("grund", ["bg-app", "bg-card"])
def test_text_kontrast_mindestens_4_5(vordergrund, grund):
    assert _kontrast(_var(vordergrund), _var(grund)) >= 4.5


def test_text_dim_wert():
    assert _var("text-dim").lower() == "#8794a8"


def test_fokusring_fuer_bedienelemente():
    for selektor in ("a:focus-visible", "button:focus-visible", "summary:focus-visible", ".nav-tab:focus-visible"):
        assert selektor in BASE
    assert re.search(r":focus-visible[^{]*\{[^}]*outline:\s*2px solid", BASE)