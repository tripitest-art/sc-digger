"""Hilfe-Seite `/hilfe` „So funktioniert sc-digger“ (Issue #711).

Kein Navigationsbereich: Der Link „Hilfe“ im Kopf jeder Seite führt hierher.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from ..common import _render
from ..hilfe import STATIONEN, TAG_SCHRITTE

router = APIRouter()


@router.get("/hilfe", response_class=HTMLResponse)
def hilfe_page():
    return _render("hilfe.html", stationen=STATIONEN, schritte=TAG_SCHRITTE)


def register(app) -> None:
    """Hängt die Hilfe-Route an die App."""
    # Routen direkt anhängen statt include_router, wie bei track_detail: so bleibt die
    # Reihenfolge der übrigen Routen unverändert.
    app.router.routes.extend(router.routes)
    if hasattr(app.router, "_mark_routes_changed"):
        app.router._mark_routes_changed()
