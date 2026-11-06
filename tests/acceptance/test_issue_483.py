"""Akzeptanztests: Inline-Styles in Status und Tracks durch Klassen ersetzt."""
import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[2] / "sc_digger" / "web" / "templates"


def _static_styles(name):
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    return [s for s in re.findall(r'style="([^"]*)"', text) if "{{" not in s]


@pytest.mark.parametrize("name", ["status.html", "tracks.html"])
def test_hoechstens_sechs_statische_inline_styles(name):
    assert len(_static_styles(name)) <= 6


def test_basisklassen_und_color_scheme_in_base():
    css = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert re.search(r"color-scheme:\s*dark", css)
    for klasse in (".page-head", ".meta-sm", ".btn-sm", ".mt-1", ".mb-0"):
        assert re.search(re.escape(klasse) + r"\s*\{", css), klasse
