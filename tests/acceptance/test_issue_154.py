"""Akzeptanztests: Config + Schema für Multi-Platform-Scout (Bandcamp & Beatport)."""
from pathlib import Path

import pytest
import yaml


def _load_raw():
    path = Path(__file__).resolve().parents[2] / "config.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_scout_section_present():
    """config.yaml enthält den scout-Abschnitt mit bandcamp.feeds und beatport.charts."""
    raw = _load_raw()
    assert "scout" in raw, "scout-Abschnitt fehlt"
    assert "bandcamp" in raw["scout"], "scout.bandcamp fehlt"
    assert "feeds" in raw["scout"]["bandcamp"], "scout.bandcamp.feeds fehlt"
    assert isinstance(raw["scout"]["bandcamp"]["feeds"], list)
    assert "beatport" in raw["scout"], "scout.beatport fehlt"
    assert "charts" in raw["scout"]["beatport"], "scout.beatport.charts fehlt"
    assert isinstance(raw["scout"]["beatport"]["charts"], list)


def test_scout_feeds_defaults_to_empty():
    """Ohne manuelle Konfiguration sind die Feed-Listen leer."""
    raw = _load_raw()
    assert raw["scout"]["bandcamp"]["feeds"] == []
    assert raw["scout"]["beatport"]["charts"] == []


def test_validate_scout_valid():
    """Schema-Validierung akzeptiert gültige scout-Konfiguration."""
    from sc_digger.schema import validate_config

    problems = validate_config({
        "scout": {
            "bandcamp": {"feeds": ["https://label.bandcamp.com/feed"]},
            "beatport": {"charts": ["https://www.beatport.com/genre/hard-techno/2/top-100"]},
        },
        "search": {"tags": ["test"]},  # Minimal-Search, damit validate_config nicht meckert
    })
    errors = [p for p in problems if p.level == "error"]
    assert errors == [], f"unerwartete Fehler: {errors}"


def test_validate_scout_rejects_invalid():
    """Schema-Validierung erkennt falsche Typen in scout."""
    from sc_digger.schema import validate_config

    problems = validate_config({
        "scout": {
            "bandcamp": {"feeds": "keine-liste-sondern-string"},
            "beatport": {"charts": []},
        },
        "search": {"tags": ["test"]},
    })
    errors = [p for p in problems if p.level == "error"]
    assert len(errors) >= 1
