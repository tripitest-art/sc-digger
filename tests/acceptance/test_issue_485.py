"""Akzeptanztests: Icons statt Emoji in Navigation und Feedback."""
import re
from pathlib import Path

from tests.test_web_tracks_ui import PASSWORD, client_for

TEMPLATES = Path(__file__).resolve().parents[2] / "sc_digger" / "web" / "templates"
EMOJI = ("🎧", "🛒", "⚠️", "👥", "⏳", "⚡")


def _get(tmp_path, pfad):
    return client_for(tmp_path).get(pfad, auth=("sc", PASSWORD))


def test_makro_datei_mit_allen_namen():
    text = (TEMPLATES / "_icons.html").read_text(encoding="utf-8")
    assert "macro icon(" in text
    for name in ("like", "dislike", "later", "gate", "store", "warning", "duplicates", "match", "headphones"):
        assert name in text


def test_kopfzeile_ohne_emoji(tmp_path):
    kopf = re.search(r"<header.*?</header>", _get(tmp_path, "/").text, re.S).group(0)
    assert not any(e in kopf for e in EMOJI)
    assert "<svg" in kopf


def test_digest_chips_ohne_emoji(tmp_path):
    text = _get(tmp_path, "/digest").text
    assert "⏳ Offene Gates" not in text
    assert "🛒 Kaufliste" not in text


def test_template_dateien_ohne_feedback_emoji():
    for name in ("tracks.html", "kaufliste.html"):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        buttons = re.findall(r"<button[^>]*sendFeedback[^>]*>.*?</button>", text, re.S)
        assert buttons, name
        for b in buttons:
            assert "aria-label=" in b
            assert "aria-pressed=" in b
            assert not any(e in b for e in ("👍", "👎", "⏳"))