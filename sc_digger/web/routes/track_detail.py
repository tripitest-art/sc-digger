"""Track-Detailseite mit Lebenslauf (`/track/sc/<sc_id>`, Teil 1 von #590, Issue #615).

Die Seite gehört zu keinem Navigationsbereich: Man erreicht sie über einen direkten Link
(Links aus Digest und Track-Explorer folgen in Teil 2).
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..common import _render
from ..track_life import collect_track_life

router = APIRouter()


def safe_link(value: str | None) -> str | None:
    """value, wenn es mit "https://" oder "http://" beginnt, sonst None."""
    # Gate- und Store-Adressen stammen von fremden Seiten. Nur http(s) wird verlinkt, damit
    # etwa ein „javascript:“-Link nie als anklickbares href auf der Seite landet.
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        return value
    return None


@router.get("/track/sc/{sc_id}", response_class=HTMLResponse)
def track_life_page(request: Request, sc_id: str):
    # Als Text annehmen und selbst umwandeln: eine kaputte ID soll dieselbe freundliche
    # 404-Seite bekommen wie eine unbekannte, nicht FastAPIs JSON-Fehler 422.
    try:
        parsed = int(sc_id)
    except ValueError:
        parsed = None
    life = collect_track_life(request.app.state.cfg, parsed) if parsed is not None else None
    if life is None:
        return _render("track.html", status_code=404, life=None, sc_id=sc_id, safe_link=safe_link)
    return _render("track.html", life=life, sc_id=sc_id, safe_link=safe_link)


def register(app) -> None:
    """Hängt die Route der Track-Detailseite an die App."""
    app.router.routes.extend(router.routes)
    if hasattr(app.router, "_mark_routes_changed"):
        app.router._mark_routes_changed()
