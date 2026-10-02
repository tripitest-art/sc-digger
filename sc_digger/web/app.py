"""FastAPI-App der Statusseite: nur lesend, HTTP-Basic geschützt, ohne API-Dokumentation."""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..models import Config
from .status import collect_status

_TEMPLATES = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES)),
    autoescape=select_autoescape(("html", "xml")),
)


def _unauthorized() -> PlainTextResponse:
    return PlainTextResponse(
        "Anmeldung erforderlich",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="sc-digger"'},
    )


def _check_auth(request: Request, password: str | None, allow_anonymous: bool) -> bool:
    if allow_anonymous:
        return True
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("basic "):
        return False
    import base64

    try:
        decoded = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
    except Exception:
        return False
    _, _, given = decoded.partition(":")
    return secrets.compare_digest(given, password or "")


def create_app(cfg: Config, *, password: str | None, allow_anonymous: bool = False) -> FastAPI:
    """Erzeugt die Status-App.

    password leer/None und allow_anonymous False: ValueError (nie versehentlich offen starten).
    Keine API-Dokumentation ausliefern. app.state.cfg = cfg.
    """
    if not password and not allow_anonymous:
        raise ValueError("SC_DIGGER_WEB_PASSWORD ist nicht gesetzt; Start abgelehnt")

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cfg = cfg
    app.state.password = password
    app.state.allow_anonymous = allow_anonymous

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        snap = collect_status(cfg)
        template = _env.get_template("status.html")
        return HTMLResponse(template.render(snap=snap))

    @app.get("/api/status")
    def api_status(request: Request):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        return JSONResponse(collect_status(cfg).to_dict())

    return app
