"""Tests für die Auslagerung von Inline-Styles in CSS-Klassen (Issue #483).

Prüft:
- Basisklassen und color-scheme in base.html
- Statische Inline-Styles in status.html und tracks.html (<= 6)
- Alle Dashboard-Seiten liefern HTTP 200 und rendern die neuen Klassen
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "sc_digger" / "web" / "templates"
PASSWORD = "test-styles-pw"
AUTH = ("sc", PASSWORD)


def _make_cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def _seed(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.upsert_track(TrackRecord(
            path="inbox/150/test.wav", mtime=1.0, size=1000,
            artist="Svetec", title="Acid Kick", format="wav",
            quality_status=QualityStatus.OK, bpm=150.0, key_camelot="8A",
            status=TrackStatus.INBOX,
            source_url="https://soundcloud.com/svetec/1",
        ))
    return db_path


def test_base_css_has_dark_color_scheme_and_required_classes():
    css = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert re.search(r"color-scheme:\s*dark", css)
    expected_classes = (
        ".page-head", ".meta-sm", ".btn-sm", ".mt-1", ".mb-0",
        ".m-0", ".mt-0", ".mt-02", ".mt-09", ".mb-035", ".fw-600",
        ".fs-80", ".fs-84", ".pill-sm", ".btn-xs", ".btn-preview",
        ".grid-2col", ".flex-between", ".flex-col-end", ".flex-wrap-gap",
        ".text-main",
    )
    for klasse in expected_classes:
        assert re.search(re.escape(klasse) + r"\s*\{", css), f"Klasse {klasse} fehlt in base.html"


def test_css_cascade_order_utilities_override_components():
    """Hilfsklassen müssen nach Komponentenklassen stehen, damit sie bei gleicher Spezifität gewinnen."""
    css = (TEMPLATES / "base.html").read_text(encoding="utf-8")

    # .grid-2col nach .stat-grid
    assert css.find(".stat-grid") < css.rfind(".grid-2col"), ".grid-2col muss nach .stat-grid definiert sein"
    # .btn-xs und .btn-preview nach .btn-page
    assert css.find(".btn-page") < css.rfind(".btn-xs"), ".btn-xs muss nach .btn-page definiert sein"
    assert css.find(".btn-page") < css.rfind(".btn-preview"), ".btn-preview muss nach .btn-page definiert sein"
    # .mb-0 und .alert-compact nach .alert-box
    assert css.find(".alert-box") < css.rfind(".mb-0"), ".mb-0 muss nach .alert-box definiert sein"
    assert css.find(".alert-box") < css.rfind(".alert-compact"), ".alert-compact muss nach .alert-box definiert sein"

    # Utility-Block muss vor den Media Queries stehen
    media_pos = css.find("@media")
    assert media_pos > 0, "@media fehlt in base.html"
    assert css.rfind(".grid-2col") < media_pos
    assert css.rfind(".btn-xs") < media_pos


def test_all_classes_in_status_and_tracks_are_defined():
    """Jede statische Klasse in status.html und tracks.html muss im CSS definiert sein."""
    base_css = (TEMPLATES / "base.html").read_text(encoding="utf-8")

    for template_name in ("status.html", "tracks.html"):
        content = (TEMPLATES / template_name).read_text(encoding="utf-8")
        local_match = re.search(r"<style>(.*?)</style>", content, re.DOTALL)
        local_css = local_match.group(1) if local_match else ""
        combined_css = base_css + "\n" + local_css

        # Alle Klassennamen aus class="..." extrahieren (Jinja-Blöcke herausfiltern)
        classes: set[str] = set()
        for raw_class in re.findall(r'class="([^"]+)"', content):
            cleaned = re.sub(r'\{\{[^}]+\}\}', ' ', raw_class)
            cleaned = re.sub(r'\{%[^%]+%\}', ' ', cleaned)
            for token in cleaned.split():
                token = token.strip()
                if token and not token.startswith(('"', "'", "{", "%")):
                    classes.add(token)

        # match-btn ist ein semantischer Hook im Template
        ignored = {"match-btn"}
        for cls in sorted(classes - ignored):
            assert re.search(r"\." + re.escape(cls) + r"[\s,.:{#>]", combined_css), (
                f"Klasse .{cls} in {template_name} ist im Stylesheet nicht definiert!"
            )


def test_no_static_inline_styles_in_status_and_tracks():
    for name in ("status.html", "tracks.html"):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        static_styles = [s for s in re.findall(r'style="([^"]*)"', text) if "{{" not in s]
        assert len(static_styles) == 0, f"Unerwartete statische Inline-Styles in {name}: {static_styles}"


@pytest.mark.parametrize("route", [
    "/",
    "/tracks",
    "/digest",
    "/mix",
    "/kaufliste",
    "/intake",
    "/rejected",
])
def test_all_pages_return_200_and_contain_dark_theme(tmp_path: Path, route: str):
    _seed(tmp_path)
    cfg = _make_cfg(tmp_path)
    client = TestClient(create_app(cfg, password=PASSWORD))
    resp = client.get(route, auth=AUTH)
    assert resp.status_code == 200, f"Route {route} lieferte Status {resp.status_code}"
    assert "color-scheme: dark" in resp.text


def test_status_page_uses_new_classes(tmp_path: Path):
    _seed(tmp_path)
    cfg = _make_cfg(tmp_path)
    client = TestClient(create_app(cfg, password=PASSWORD))
    resp = client.get("/", auth=AUTH)
    assert resp.status_code == 200
    assert "hero-title" in resp.text
    assert "flex-col-end" in resp.text
    assert "btn-sm" in resp.text or "pill-sm" in resp.text


def test_tracks_page_uses_new_classes(tmp_path: Path):
    _seed(tmp_path)
    cfg = _make_cfg(tmp_path)
    client = TestClient(create_app(cfg, password=PASSWORD))
    resp = client.get("/tracks", auth=AUTH)
    assert resp.status_code == 200
    assert "flex-wrap-gap" in resp.text
    assert "btn-sm" in resp.text
    assert "meta-sm" in resp.text
    assert "fw-600" in resp.text
