"""Tests für das Jinja-Makro icon() und dessen Einbindung im Web-Dashboard (Issue #485)."""
from pathlib import Path
import jinja2
import pytest

from tests.test_web_tracks_ui import PASSWORD, client_for, seed as seed_tracks_ui
from tests.test_web_kaufliste import seed_items
from tests.test_web_feedback import seed_digest_dbs, seed_track_db

TEMPLATES = Path(__file__).resolve().parents[1] / "sc_digger" / "web" / "templates"
ALL_ICON_NAMES = (
    "like",
    "dislike",
    "later",
    "gate",
    "store",
    "warning",
    "duplicates",
    "match",
    "headphones",
)


@pytest.fixture
def jinja_env() -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATES),
        autoescape=jinja2.select_autoescape(["html", "xml"]),
    )


@pytest.mark.parametrize("name", ALL_ICON_NAMES)
def test_icon_macro_renders_all_supported_icons(jinja_env, name):
    tmpl = jinja_env.from_string(
        '{% from "_icons.html" import icon %}{{ icon(name) }}'
    )
    rendered = tmpl.render(name=name).strip()

    assert rendered.startswith("<svg")
    assert rendered.endswith("</svg>")
    assert f'class="icon icon-{name}"' in rendered
    assert 'width="16"' in rendered
    assert 'height="16"' in rendered
    assert 'viewBox="0 0 24 24"' in rendered
    assert 'fill="none"' in rendered
    assert 'stroke="currentColor"' in rendered
    assert 'stroke-width="2"' in rendered
    assert 'stroke-linecap="round"' in rendered
    assert 'stroke-linejoin="round"' in rendered
    assert 'aria-hidden="true"' in rendered


def test_icon_macro_custom_size(jinja_env):
    tmpl = jinja_env.from_string(
        '{% from "_icons.html" import icon %}{{ icon("headphones", size=24) }}'
    )
    rendered = tmpl.render().strip()
    assert 'width="24"' in rendered
    assert 'height="24"' in rendered


def test_icon_macro_unknown_name_renders_empty_svg(jinja_env):
    tmpl = jinja_env.from_string(
        '{% from "_icons.html" import icon %}{{ icon("unknown_icon") }}'
    )
    rendered = tmpl.render().strip()
    assert rendered.startswith("<svg")
    assert rendered.endswith("</svg>")
    assert 'class="icon icon-unknown_icon"' in rendered


def test_tracks_page_renders_feedback_icons_and_accessibility(tmp_path):
    seed_track_db(tmp_path, sc_id=101)
    client = client_for(tmp_path)
    r = client.get("/tracks", auth=("sc", PASSWORD))
    assert r.status_code == 200

    # Feedback-Icons statt Emoji
    assert "icon-like" in r.text
    assert "icon-dislike" in r.text
    assert "icon-later" in r.text
    assert 'aria-label="Gefällt mir"' in r.text
    assert 'aria-label="Passt nicht"' in r.text
    assert 'aria-label="Später anhören"' in r.text
    assert 'aria-pressed="false"' in r.text or 'aria-pressed="true"' in r.text

    # Chips mit SVG-Icons
    assert "icon-warning" in r.text
    assert "icon-duplicates" in r.text


def test_tracks_page_renders_match_icon(tmp_path):
    seed_tracks_ui(tmp_path)
    client = client_for(tmp_path)
    r = client.get("/tracks", auth=("sc", PASSWORD))
    assert r.status_code == 200
    assert "icon-match" in r.text


def test_kaufliste_page_renders_feedback_icons_and_accessibility(tmp_path):
    seed_items(tmp_path / "tracks.sqlite", count=2)
    client = client_for(tmp_path)
    r = client.get("/kaufliste", auth=("sc", PASSWORD))
    assert r.status_code == 200

    assert "icon-like" in r.text
    assert "icon-dislike" in r.text
    assert "icon-later" in r.text
    assert 'aria-label="Gefällt mir"' in r.text
    assert 'aria-label="Passt nicht"' in r.text
    assert 'aria-label="Später anhören"' in r.text


def test_digest_page_renders_gate_and_store_icons(tmp_path):
    seed_digest_dbs(tmp_path, sc_id=101)
    client = client_for(tmp_path)
    r = client.get("/digest", auth=("sc", PASSWORD))
    assert r.status_code == 200

    assert "icon-gate" in r.text
    assert "icon-store" in r.text
    assert "Offene Gates" in r.text
    assert "Kaufliste (nur Store)" in r.text
