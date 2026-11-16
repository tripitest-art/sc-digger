"""Akzeptanztests: Tastatur-Workflow im Dashboard."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TPL = ROOT / "sc_digger" / "web" / "templates"
BASE = (TPL / "base.html").read_text(encoding="utf-8")
TRACKS = (TPL / "tracks.html").read_text(encoding="utf-8")
DIGEST = (TPL / "digest.html").read_text(encoding="utf-8")
ALLE = "\n".join(p.read_text(encoding="utf-8") for p in sorted(TPL.glob("*.html")))


def _taste_vorhanden(text: str, *zeichen: str) -> list[str]:
    """Sucht Zuweisungen wie ``case '+'`` oder ``e.key === '+'`` — Anführungszeichen egal."""
    fehlend = []
    for z in zeichen:
        muster = rf"===\s*['\"]{re.escape(z)}['\"]|case\s+['\"]{re.escape(z)}['\"]"
        if not re.search(muster, text):
            fehlend.append(z)
    return fehlend


def test_bewertungstasten_sind_gebunden():
    assert _taste_vorhanden(TRACKS, "+", "-", "s", "z") == []


def test_shortcuts_ignorieren_formularziele():
    treffer = re.findall(r"closest\(\s*(['\"])([^'\"]+)\1\s*\)", TRACKS)
    auswahlen = [{t.strip() for t in treffer_text.split(",")} for _, treffer_text in treffer]
    pflicht = {"button", "a", "summary", "input", "select", "textarea"}
    assert any(pflicht <= auswahl for auswahl in auswahlen), (
        "kein Keydown-Handler prüft per closest() auf Formularziele: "
        f"{auswahlen}"
    )


def test_feedback_wird_genau_einmal_definiert():
    definitionen = re.findall(r"function\s+sendFeedback\s*\(", ALLE)
    assert len(definitionen) == 1, f"{len(definitionen)} Definitionen gefunden"
    assert re.search(r"function\s+sendFeedback\s*\(", BASE)
    assert not re.search(r"function\s+sendFeedback\s*\(", DIGEST)


def test_digest_nutzt_die_gemeinsame_rueckmeldung():
    assert "sendFeedback(" in DIGEST
    assert "aria-pressed" in DIGEST


def test_digest_nutzt_die_icons_der_seite():
    for name in ("like", "dislike", "later"):
        assert f'icon("{name}"' in DIGEST, f"Icon {name} fehlt im Digest"


def test_player_leiste_nimmt_keine_tab_stopps_mehr():
    assert "visibility: hidden" in BASE
    assert "visibility: visible" in BASE


def test_fokusringe_sind_sichtbar():
    assert ":focus-visible" in BASE
    assert "outline: none" not in BASE
    assert "outline: none" not in TRACKS
