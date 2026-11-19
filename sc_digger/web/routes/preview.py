"""Hörproben aus dem SoundCloud-Stream: `GET /api/preview/sc/{sc_id}` (Befund 8, Teil 1)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from .. import stream_preview

router = APIRouter()


@router.get("/api/preview/sc/{sc_id}")
async def api_preview_sc(request: Request, sc_id: int):
    cfg = request.app.state.cfg
    if not stream_preview.preview_enabled(cfg):
        return JSONResponse(
            {"ok": False, "error": "Hörproben sind aus (web.stream_preview)."}, status_code=404
        )
    # ffmpeg und die SoundCloud-Abfrage blockieren: nicht auf der Ereignisschleife laufen lassen.
    pfad = await run_in_threadpool(stream_preview.get_or_build_preview, cfg, sc_id)
    if pfad is None:
        return JSONResponse({"ok": False, "error": "Keine Hörprobe verfügbar."}, status_code=404)
    return FileResponse(pfad, media_type="audio/ogg")


def register(app) -> None:
    """Hängt die Route der Hörprobe an die App."""
    app.router.routes.extend(router.routes)
    if hasattr(app.router, "_mark_routes_changed"):
        app.router._mark_routes_changed()
