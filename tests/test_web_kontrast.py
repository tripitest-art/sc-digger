"""Tests für Farbkontraste und Tastaturfokus im Web-Dashboard (Issue #486)."""
import re
from pathlib import Path
import pytest

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "sc_digger" / "web" / "templates"
BASE_HTML = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")


def _extract_var(name: str) -> str:
    match = re.search(r"--" + re.escape(name) + r":\s*(#[0-9a-fA-F]{6})\b", BASE_HTML)
    assert match, f"CSS-Variable --{name} nicht in base.html gefunden"
    return match.group(1)


def _relative_luminance(hex_color: str) -> float:
    channels = [int(hex_color[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]
    linear = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    l1, l2 = sorted((_relative_luminance(hex_a), _relative_luminance(hex_b)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def test_text_dim_color_value():
    """Stellt sicher, dass --text-dim auf den angehobenen Kontrastwert gesetzt ist."""
    dim = _extract_var("text-dim")
    assert dim.lower() == "#8794a8"


@pytest.mark.parametrize(
    "fg_name",
    [
        "text-main",
        "text-muted",
        "text-dim",
        "accent-ok",
        "accent-warn",
        "accent-err",
        "accent-primary",
    ],
)
@pytest.mark.parametrize("bg_name", ["bg-app", "bg-card"])
def test_text_contrast_wcag_aa(fg_name: str, bg_name: str):
    """Prüft, dass alle Text- und Akzentfarben auf den Dashboard-Hintergründen WCAG AA (>= 4.5:1) erfüllen."""
    fg = _extract_var(fg_name)
    bg = _extract_var(bg_name)
    ratio = _contrast_ratio(fg, bg)
    assert ratio >= 4.5, f"Kontrast von {fg_name} ({fg}) auf {bg_name} ({bg}) ist {ratio:.2f}:1 (< 4.5:1)"


def test_focus_visible_selectors_present():
    """Stellt sicher, dass :focus-visible auf Navigation, Links, Buttons und Summary angewendet wird."""
    required_selectors = [
        "a:focus-visible",
        "button:focus-visible",
        "summary:focus-visible",
        ".nav-tab:focus-visible",
    ]
    for selector in required_selectors:
        assert selector in BASE_HTML, f"Selektor {selector} fehlt in base.html"


def test_focus_visible_rule_properties():
    """Prüft, dass der :focus-visible-Ring eine 2px-Kontur mit Offset und Akzentfarbe verwendet."""
    match = re.search(
        r"(?:a|button|summary|\.nav-tab):focus-visible[^{]*\{([^}]+)\}",
        BASE_HTML,
    )
    assert match, "Kein passender CSS-Block für :focus-visible gefunden"
    block = match.group(1)
    assert re.search(r"outline:\s*2px\s+solid\s+var\(--accent-primary\)", block)
    assert re.search(r"outline-offset:\s*2px", block)


def test_focus_not_restricted_to_inputs():
    """Stellt sicher, dass Tastaturfokus nicht nur auf Texteingaben (input/textarea) beschränkt ist."""
    input_focus = re.findall(r"input[^:]*:focus|textarea:focus|select:focus", BASE_HTML)
    interactive_focus = re.findall(r"(?:a|button|summary|\.nav-tab):focus-visible", BASE_HTML)
    assert len(input_focus) > 0, "Bestehende Input-Fokusregeln sollten weiterhin existieren"
    assert len(interactive_focus) >= 4, "Bedienelemente müssen eigene Fokusstile haben"
