"""MixesDB im Web-Dashboard: Set-Tracklist suchen, lesen und gegen die Sammlung halten.

Die Seite ist rein lesend. Sie ruft das Netz über ``run_in_threadpool`` auf, damit die
Ereignisschleife nicht blockiert, und öffnet die Track-DB nur mit ``mode=ro``.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from ... import mixesdb
from ...mixesdb import collection_names
from ..common import _render
from ..database import _connect_readonly, _table_exists

router = APIRouter()

MAX_TREFFER = 10


def _seite_from_query(seite: str) -> str:
    """Seitentitel aus einer eingefügten Wiki-URL oder einem Titel machen."""
    if seite.startswith("http"):
        tail = seite.split("/w/", 1)[-1]
        return tail.replace("_", " ").split("#", 1)[0].strip()
    return seite.strip()


@router.get("/mixesdb")
async def mixesdb_page(request: Request):
    params = request.query_params
    q = (params.get("q") or "").strip()
    seite = _seite_from_query(params.get("seite") or "")

    treffer: list[dict[str, str]] = []
    setlist = None
    check = None
    zaehler: dict[str, int] | None = None
    hinweis: str | None = None

    if seite:
        setlist = await run_in_threadpool(mixesdb.fetch_setlist, seite)
        if setlist is None:
            hinweis = f"Seite „{seite}“ nicht gefunden — Titel prüfen oder neu suchen."
        else:
            names = await run_in_threadpool(
                collection_names, request.app.state.cfg["state"]["track_db_path"]
            )
            check = mixesdb.check_setlist(setlist, names)
            zaehler = {
                "eintraege": len(setlist.entries),
                "fehlen": len(check.missing),
                "vorhanden": len(check.present),
                "offen": len(setlist.open_entries),
            }
            if not setlist.entries:
                hinweis = "Die Wiki-Tracklist ist leer (im Wiki als „Tracklist: none“ geführt)."
    elif q:
        titel = await run_in_threadpool(mixesdb.search_sets, q, limit=MAX_TREFFER)
        treffer = [
            {"titel": name, "url": "?seite=" + quote_plus(name)} for name in titel
        ]
        if not treffer:
            hinweis = f"Keine MixesDB-Seite zu „{q}“ gefunden."

    return _render(
        "mixesdb.html",
        q=q,
        seite=seite,
        treffer=treffer,
        setlist=setlist,
        check=check,
        zaehler=zaehler,
        hinweis=hinweis,
        max_treffer=MAX_TREFFER,
    )


def register(app) -> None:
    """Hängt die MixesDB-Seite an die App."""
    app.router.routes.extend(router.routes)
    if hasattr(app.router, "_mark_routes_changed"):
        app.router._mark_routes_changed()
