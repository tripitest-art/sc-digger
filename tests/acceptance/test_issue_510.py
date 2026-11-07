"""Akzeptanztests: Router je Bereich, create_app bleibt der einzige Einstieg."""
from __future__ import annotations

import inspect
from pathlib import Path

from sc_digger.web.app import create_app

WEB = Path(__file__).resolve().parents[2] / "sc_digger" / "web"
BEREICHE = ("status", "tracks", "mix", "digest", "intake", "config")


def _routen(route_defs) -> set[tuple[str, str]]:
    return {(m, r.path) for r in route_defs for m in getattr(r, "methods", []) or []}


def _alle_routen(app) -> set[tuple[str, str]]:
    return {(m, r.path) for r in app.routes for m in getattr(r, "methods", []) or []}


def test_jede_domain_hat_ein_eigenes_modul():
    for name in BEREICHE:
        modul = WEB / "routes" / f"{name}.py"
        if not modul.is_file():
            continue  # Bereich noch nicht umgezogen (Folge-Issue)
        text = modul.read_text(encoding="utf-8")
        assert "def register(app)" in text, f"{name}.py hat kein register(app)"


def test_tracks_ist_umgezogen():
    text = (WEB / "routes" / "tracks.py").read_text(encoding="utf-8")
    for pfad in ('"/tracks"', '"/api/tracks"', '"/api/audio/{track_id}"',
                 '"/duplicates"', '"/api/duplicates"'):
        assert pfad in text, f"{pfad} fehlt in routes/tracks.py"


def test_app_py_ist_fuer_tracks_entlastet():
    text = (WEB / "app.py").read_text(encoding="utf-8")
    for pfad in ('@app.get("/tracks")', '@app.get("/api/tracks")',
                 '@app.get("/duplicates")', '@app.get("/api/duplicates")'):
        assert pfad not in text, f"{pfad} steht noch in app.py"


def test_trackdb_helfer_liegen_in_common():
    text = (WEB / "common.py").read_text(encoding="utf-8")
    assert "class _ReadOnlyTrackDB" in text
    assert "class _ReadOnlyTrackDB" not in (WEB / "app.py").read_text(encoding="utf-8")


def test_keine_route_doppelt_registriert():
    text = "\n".join(p.read_text(encoding="utf-8") for p in (WEB / "routes").glob("*.py"))
    quelle = text + (WEB / "app.py").read_text(encoding="utf-8")
    for pfad in ("/tracks", "/duplicates", "/api/tracks", "/api/duplicates"):
        assert quelle.count(f'"{pfad}"') == 1, f"{pfad} mehrfach registriert"
