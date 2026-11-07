"""Routen für den Track-Explorer, Audio-Vorhören und die Duplikat-Ansicht."""
from __future__ import annotations

import json
import math
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool

from ...db import TrackDB
from ..audio import resolve_playable_path
from ..common import (
    _audio_response,
    _playable_roots,
    _query_bool,
    _query_float,
    _query_int,
    _render,
    _require_json_content_type,
)
from ..database import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    query_duplicate_groups,
    query_similar_tracks,
    query_tracks,
    taste_model_info,
)
from ..inbox_cleanup import list_inbox_duplicates, move_inbox_duplicates

router = APIRouter()

FILTER_LABELS: dict[str, str] = {
    "q": "Suche",
    "status": "Status",
    "quality": "Qualität",
    "key_camelot": "Key",
    "bpm_min": "BPM ab",
    "bpm_max": "BPM bis",
    "adopted": "Übernahme",
    "breakdown_max": "Max. Breakdown %",
    "taste_min": "Min. Geschmack",
}


def match_url(key_camelot: str | None, bpm: float | int | None) -> str | None:
    """Erzeugt den Link zum Harmonic Mix Finder (`/mix?key=...&bpm=...`) für einen Track.

    - Ist `key_camelot` leer/`None` oder kein gültiger String nach `.strip()`, oder ist `bpm`
      `None` bzw. `<= 0`, wird `None` zurückgegeben.
    - Sonst wird `"/mix?" + urlencode({"key": key_camelot.strip().upper(), "bpm": f"{float(bpm):g}"})`
      zurückgegeben (z. B. `"/mix?key=8A&bpm=155"` oder `"/mix?key=8A&bpm=155.5"`).
    """
    if not key_camelot or not isinstance(key_camelot, str):
        return None
    cleaned_key = key_camelot.strip().upper()
    if not cleaned_key:
        return None
    if bpm is None or isinstance(bpm, bool):
        return None
    try:
        val_bpm = float(bpm)
    except (ValueError, TypeError):
        return None
    if math.isnan(val_bpm) or math.isinf(val_bpm) or val_bpm <= 0:
        return None
    return "/mix?" + urlencode({"key": cleaned_key, "bpm": f"{val_bpm:g}"})


def active_filter_chips(
    filters: dict[str, str],
    *,
    limit: int,
    sort: str | None = None,
    direction: str = "asc",
) -> list[dict[str, str]]:
    """Ein Eintrag je Filter mit nicht leerem Wert, in der Reihenfolge von `filters`:
    {"key": <Filterschlüssel>, "label": <deutsche Bezeichnung>, "value": <Wert>,
     "remove_url": <"/tracks?" + urlencode(...) ohne diesen Filter, mit limit, mit sort/dir falls sort gesetzt>}.
    Leere Filter ergeben []. Wirft nicht."""
    try:
        chips: list[dict[str, str]] = []
        if not filters:
            return chips
        active = {k: v for k, v in filters.items() if v is not None and str(v) != ""}
        for key, val in active.items():
            query: dict[str, str] = {
                k: str(v)
                for k, v in active.items()
                if k != key
            }
            query["limit"] = str(limit)
            if sort:
                query["sort"] = str(sort)
                query["dir"] = str(direction)
            remove_url = "/tracks?" + urlencode(query)
            chips.append({
                "key": key,
                "label": FILTER_LABELS.get(key, key),
                "value": str(val),
                "remove_url": remove_url,
            })
        return chips
    except Exception:
        return []


@router.get("/api/tracks")
def api_tracks(request: Request):
    cfg = request.app.state.cfg
    params = request.query_params
    sort = params.get("sort") or None
    direction = (params.get("dir") or "asc").lower()
    result = query_tracks(
        cfg["state"]["track_db_path"],
        status=params.get("status") or None,
        search=params.get("q") or None,
        bpm_min=_query_float(params.get("bpm_min")),
        bpm_max=_query_float(params.get("bpm_max")),
        key_camelot=params.get("key_camelot") or None,
        quality_status=params.get("quality") or None,
        adopted=_query_bool(params.get("adopted")),
        breakdown_ratio_max=_query_float(params.get("breakdown_max")),
        taste_min=_query_float(params.get("taste_min")),
        sort=sort,
        descending=(direction == "desc"),
        limit=_query_int(params.get("limit"), DEFAULT_LIMIT),
        offset=_query_int(params.get("offset"), 0),
    )
    return JSONResponse(result.to_dict())


# Klangliche Ähnlichkeitssuche im Track-Explorer (Issue #493)
@router.get("/api/tracks/{track_id}/similar")
def api_track_similar(request: Request, track_id: int):
    raw_top_n = request.query_params.get("top_n")
    top_n = _query_int(raw_top_n, 5)
    top_n = min(max(top_n, 1), 20)
    track_db_path = request.app.state.cfg["state"]["track_db_path"]
    result = query_similar_tracks(track_db_path, track_id, top_n=top_n)
    if result is None:
        return JSONResponse({"detail": "Track nicht gefunden"}, status_code=404)
    return JSONResponse(result)


# Vorhören im Track-Explorer (Issue #350): liefert die lokale Datei aus Inbox oder
# Sammlung, nur lesend und nur innerhalb der konfigurierten Wurzeln. Range-Requests
# erlauben das Springen im Track, ohne die Datei komplett zu laden.
@router.get("/api/audio/{track_id}")
def api_audio(request: Request, track_id: int):
    cfg = request.app.state.cfg
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        track = db.get_track_by_id(track_id)
    if track is None:
        return PlainTextResponse("Track nicht gefunden", status_code=404)
    return _audio_response(request, track.path, cfg)


# DJ-Ansicht auf dieselben rein lesenden Abfragen (Teil 2 des Dashboards): Filter-Formular
# und Treffertabelle serverseitig gerendert, Paginierung über offset.
@router.get("/tracks", response_class=HTMLResponse)
def tracks_page(request: Request):
    cfg = request.app.state.cfg
    params = request.query_params
    filters = {
        "q": params.get("q") or "",
        "status": params.get("status") or "",
        "quality": params.get("quality") or "",
        "key_camelot": params.get("key_camelot") or "",
        "bpm_min": params.get("bpm_min") or "",
        "bpm_max": params.get("bpm_max") or "",
        "adopted": params.get("adopted") or "",
        "breakdown_max": params.get("breakdown_max") or "",
        "taste_min": params.get("taste_min") or "",
    }
    raw_limit = params.get("limit")
    limit_val = DEFAULT_LIMIT if raw_limit == "" else _query_int(raw_limit, DEFAULT_LIMIT)
    page_size = max(1, min(limit_val, MAX_LIMIT))
    offset = max(0, _query_int(params.get("offset"), 0))
    sort = params.get("sort") or None
    direction = (params.get("dir") or "asc").lower()
    descending = (direction == "desc")

    result = query_tracks(
        cfg["state"]["track_db_path"],
        status=filters["status"] or None,
        search=filters["q"] or None,
        bpm_min=_query_float(filters["bpm_min"]),
        bpm_max=_query_float(filters["bpm_max"]),
        key_camelot=filters["key_camelot"] or None,
        quality_status=filters["quality"] or None,
        adopted=_query_bool(filters["adopted"]),
        breakdown_ratio_max=_query_float(filters["breakdown_max"]),
        taste_min=_query_float(filters["taste_min"]),
        sort=sort,
        descending=descending,
        limit=page_size,
        offset=offset,
    )

    # Nur Zeilen mit vorhandener lokaler Datei bekommen einen Abspiel-Knopf.
    roots = _playable_roots(cfg)
    for item in result.items:
        item["playable"] = (
            resolve_playable_path(str(item.get("path") or ""), roots) is not None
        )

    def page_url(new_offset: int) -> str:
        """Link auf eine andere Seite, aktive Filter bleiben erhalten."""
        query = {key: value for key, value in filters.items() if value}
        query["limit"] = str(page_size)
        if sort:
            query["sort"] = sort
            query["dir"] = direction
        if new_offset > 0:
            query["offset"] = str(new_offset)
        return "/tracks?" + urlencode(query)

    def sort_url(field: str) -> str:
        """Link zum Umschalten der Sortierung; Filter und Zeilenmenge bleiben erhalten."""
        query = {key: value for key, value in filters.items() if value}
        query["limit"] = str(page_size)
        query["sort"] = field
        if sort == field:
            query["dir"] = "desc" if direction == "asc" else "asc"
        else:
            query["dir"] = "asc"
        return "/tracks?" + urlencode(query)

    total_pages = max(1, (result.total + page_size - 1) // page_size)
    filter_chips = active_filter_chips(
        filters,
        limit=page_size,
        sort=sort,
        direction=direction,
    )

    return _render(
        "tracks.html",
        tracks=result.items,
        total=result.total,
        page_size=page_size,
        page=offset // page_size + 1,
        total_pages=total_pages,
        filters=filters,
        filter_chips=filter_chips,
        taste_info=taste_model_info(cfg["state"]["track_db_path"]),
        current_sort=sort,
        current_dir=direction,
        sort_url=sort_url,
        prev_url=page_url(max(0, offset - page_size)) if offset > 0 else None,
        next_url=page_url(offset + page_size) if offset + page_size < result.total else None,
        match_url=match_url,
    )


# Duplikat-Kandidaten-Ansicht (Issue #468): Gruppen gleicher Aufnahmen per Chromaprint-Fingerprint.
@router.get("/api/duplicates")
def api_duplicates(request: Request) -> JSONResponse:
    """Gibt `{"ok": True, "total_groups": len(groups), "groups": groups}` zurück (HTTP Basic Auth, rein lesend)."""
    track_db_path = request.app.state.cfg["state"]["track_db_path"]
    groups = query_duplicate_groups(track_db_path)
    return JSONResponse({"ok": True, "total_groups": len(groups), "groups": groups})


@router.get("/duplicates", response_class=HTMLResponse)
def duplicates_page(request: Request) -> HTMLResponse:
    """Rendert `duplicates.html` mit `groups` und `total_groups = len(groups)` (HTTP Basic Auth, rein lesend)."""
    track_db_path = request.app.state.cfg["state"]["track_db_path"]
    groups = query_duplicate_groups(track_db_path)
    return _render(
        "duplicates.html",
        groups=groups,
        total_groups=len(groups),
    )


# Inbox-Dubletten derselben SoundCloud-ID (Issue #501): ansehen und überzählige Dateien
# nach _rejected/duplicate/ verschieben. Rein lesend: GET /api/inbox-duplicates.
# Verschieben: POST /api/inbox-duplicates/move.
@router.get("/api/inbox-duplicates")
def api_inbox_duplicates(request: Request):
    inbox_dir = request.app.state.cfg["download"]["inbox_dir"]
    return JSONResponse(list_inbox_duplicates(inbox_dir))


@router.post("/api/inbox-duplicates/move")
async def api_inbox_duplicates_move(request: Request):
    ct_err = _require_json_content_type(request, allow_empty_body=True)
    if ct_err is not None:
        return ct_err
    raw = await request.body()
    payload = {}
    if raw.strip():
        try:
            payload = json.loads(raw)
        except Exception:
            return JSONResponse({"ok": False, "error": "Ungültiges JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "Ungültiger Payload"}, status_code=400)

    cfg = request.app.state.cfg
    lock_dir = Path(cfg["state"]["db_path"]).parent
    sc_id = payload.get("soundcloud_id") if payload else None
    status, body = await run_in_threadpool(
        move_inbox_duplicates,
        cfg["download"]["inbox_dir"],
        lock_dir,
        soundcloud_id=sc_id,
    )
    return JSONResponse(body, status_code=status)


def register(app) -> None:
    """Hängt die Routen dieses Bereichs an die App (Reihenfolge wie bisher)."""
    app.router.routes.extend(router.routes)
    if hasattr(app.router, "_mark_routes_changed"):
        app.router._mark_routes_changed()
