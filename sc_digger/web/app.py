"""FastAPI-App der Statusseite: nur lesend, HTTP-Basic geschützt, ohne API-Dokumentation.

Ist zusätzlich ein Pfad zur lokalen Override-Datei gesetzt (Issue #145), kommen die schreibenden
Editor-Routen unter `/config` dazu. Dann wird bei jeder Anfrage die Basis- und die lokale Datei
frisch gelesen; die versionierte `config.yaml` bleibt unangetastet.
"""
from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import urlencode, urlparse

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..models import Config
from ..schema import FIELDS, SECTION_TITLES, Problem, deep_merge, get_field
from .configedit import apply_form, diff_config, format_form_value, get_path, remove_key, save_local
from .database import DEFAULT_LIMIT, MAX_LIMIT, get_collection_overview, query_digest, query_tracks
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


def _same_origin(request: Request) -> bool:
    """True, wenn Origin oder Referer denselben Host (netloc) wie der Host-Header hat.

    Browser schicken die Basic-Anmeldung auch bei Formularen fremder Seiten mit; ohne diese
    Prüfung könnte eine beliebige Seite POSTs an den Editor auslösen (CSRF).
    """
    host = request.headers.get("host", "")
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if value:
            return urlparse(value).netloc == host
    return False


def _load_yaml(path: Path) -> dict:
    """Liest eine YAML-Datei; fehlende oder leere Dateien ergeben {}."""
    if not path.is_file():
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return data if isinstance(data, dict) else {}


_TRUES = {"1", "true", "yes", "ja", "on"}
_FALSES = {"0", "false", "no", "nein", "off"}


def _query_float(value: str | None) -> float | None:
    """Query-Parameter als float; leer oder ungültig -> None (kein Filter)."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _query_int(value: str | None, default: int) -> int:
    """Query-Parameter als int; leer oder ungültig -> default."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _query_bool(value: str | None) -> bool | None:
    """Query-Parameter als bool; leer oder unbekannt -> None (kein Filter)."""
    if value is None or value == "":
        return None
    lowered = value.strip().lower()
    if lowered in _TRUES:
        return True
    if lowered in _FALSES:
        return False
    return None


def create_app(cfg: Config, *, password: str | None, allow_anonymous: bool = False,
               base_path: Path | None = None, local_path: Path | None = None) -> FastAPI:
    """Erzeugt die Status-App.

    password leer/None und allow_anonymous False: ValueError (nie versehentlich offen starten).
    Keine API-Dokumentation ausliefern. app.state.cfg = cfg.
    Nur wenn base_path UND local_path gesetzt sind, gibt es die Editor-Routen; sonst 404.
    """
    if not password and not allow_anonymous:
        raise ValueError("SC_DIGGER_WEB_PASSWORD ist nicht gesetzt; Start abgelehnt")

    # Editor-Routen gibt es nur mit beiden Pfaden; status.html verlinkt sie nur dann.
    editor_available = base_path is not None and local_path is not None

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
        overview = get_collection_overview(cfg["state"]["track_db_path"])
        template = _env.get_template("status.html")
        return HTMLResponse(template.render(
            snap=snap, overview=overview, editor_available=editor_available
        ))

    @app.get("/api/status")
    def api_status(request: Request):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        return JSONResponse(collect_status(cfg).to_dict())

    # Rein lesende Track-Abfragen für den Explorer (Teil 1 des Dashboards): nur die
    # Track-DB, ausschließlich `mode=ro`, keine Schreibpfade.
    @app.get("/api/database/overview")
    def api_database_overview(request: Request):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        return JSONResponse(get_collection_overview(cfg["state"]["track_db_path"]).to_dict())

    @app.get("/api/tracks")
    def api_tracks(request: Request):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        params = request.query_params
        result = query_tracks(
            cfg["state"]["track_db_path"],
            status=params.get("status") or None,
            search=params.get("q") or None,
            bpm_min=_query_float(params.get("bpm_min")),
            bpm_max=_query_float(params.get("bpm_max")),
            key_camelot=params.get("key_camelot") or None,
            quality_status=params.get("quality") or None,
            adopted=_query_bool(params.get("adopted")),
            limit=_query_int(params.get("limit"), DEFAULT_LIMIT),
            offset=_query_int(params.get("offset"), 0),
        )
        return JSONResponse(result.to_dict())

    # DJ-Ansicht auf dieselben rein lesenden Abfragen (Teil 2 des Dashboards): Filter-Formular
    # und Treffertabelle serverseitig gerendert, Paginierung über offset.
    @app.get("/tracks", response_class=HTMLResponse)
    def tracks_page(request: Request):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        params = request.query_params
        filters = {
            "q": params.get("q") or "",
            "status": params.get("status") or "",
            "quality": params.get("quality") or "",
            "key_camelot": params.get("key_camelot") or "",
            "bpm_min": params.get("bpm_min") or "",
            "bpm_max": params.get("bpm_max") or "",
            "adopted": params.get("adopted") or "",
        }
        page_size = max(1, min(_query_int(params.get("limit"), DEFAULT_LIMIT), MAX_LIMIT))
        offset = max(0, _query_int(params.get("offset"), 0))
        result = query_tracks(
            cfg["state"]["track_db_path"],
            status=filters["status"] or None,
            search=filters["q"] or None,
            bpm_min=_query_float(filters["bpm_min"]),
            bpm_max=_query_float(filters["bpm_max"]),
            key_camelot=filters["key_camelot"] or None,
            quality_status=filters["quality"] or None,
            adopted=_query_bool(filters["adopted"]),
            limit=page_size,
            offset=offset,
        )

        def page_url(new_offset: int) -> str:
            """Link auf eine andere Seite, aktive Filter bleiben erhalten."""
            query = {key: value for key, value in filters.items() if value}
            query["limit"] = str(page_size)
            if new_offset > 0:
                query["offset"] = str(new_offset)
            return "/tracks?" + urlencode(query)

        total_pages = max(1, (result.total + page_size - 1) // page_size)
        template = _env.get_template("tracks.html")
        return HTMLResponse(template.render(
            tracks=result.items,
            total=result.total,
            page_size=page_size,
            page=offset // page_size + 1,
            total_pages=total_pages,
            filters=filters,
            prev_url=page_url(max(0, offset - page_size)) if offset > 0 else None,
            next_url=page_url(offset + page_size) if offset + page_size < result.total else None,
        ))

    @app.get("/api/digest")
    def api_digest(request: Request):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        params = request.query_params
        result = query_digest(
            cfg["state"]["db_path"],
            cfg["state"]["track_db_path"],
            search=params.get("q") or "",
            status_filter=params.get("status") or "",
            download_kind=params.get("download_kind") or "",
            feedback=params.get("feedback") or "",
            limit=_query_int(params.get("limit"), DEFAULT_LIMIT),
            offset=_query_int(params.get("offset"), 0),
        )
        return JSONResponse(result.to_dict())

    @app.get("/digest", response_class=HTMLResponse)
    def digest_page(request: Request):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        params = request.query_params
        filters = {
            "q": params.get("q") or "",
            "status": params.get("status") or "",
            "download_kind": params.get("download_kind") or "",
            "feedback": params.get("feedback") or "",
        }
        page_size = max(1, min(_query_int(params.get("limit"), DEFAULT_LIMIT), MAX_LIMIT))
        offset = max(0, _query_int(params.get("offset"), 0))
        result = query_digest(
            cfg["state"]["db_path"],
            cfg["state"]["track_db_path"],
            search=filters["q"],
            status_filter=filters["status"],
            download_kind=filters["download_kind"],
            feedback=filters["feedback"],
            limit=page_size,
            offset=offset,
        )

        def page_url(new_offset: int) -> str:
            query = {key: value for key, value in filters.items() if value}
            query["limit"] = str(page_size)
            if new_offset > 0:
                query["offset"] = str(new_offset)
            return "/digest?" + urlencode(query)

        total_pages = max(1, (result.total + page_size - 1) // page_size)
        template = _env.get_template("digest.html")
        return HTMLResponse(template.render(
            items=result.items,
            total=result.total,
            page_size=page_size,
            page=offset // page_size + 1,
            total_pages=total_pages,
            filters=filters,
            prev_url=page_url(max(0, offset - page_size)) if offset > 0 else None,
            next_url=page_url(offset + page_size) if offset + page_size < result.total else None,
        ))

    if base_path is None or local_path is None:
        return app

    _base_path = Path(base_path)
    _local_path = Path(local_path)

    def _read_files() -> tuple[dict, dict]:
        return _load_yaml(_base_path), _load_yaml(_local_path)

    def _form_values(base: dict, local: dict, form: dict[str, str] | None) -> dict[str, str]:
        """Je editierbarem Feld der eingereichte Wert, sonst der aktuelle zusammengeführte Wert."""
        merged = deep_merge(base, local)
        values: dict[str, str] = {}
        for field in FIELDS:
            if not field.editable:
                continue
            if form is not None and field.path in form:
                values[field.path] = form[field.path]
            else:
                values[field.path] = format_form_value(field, get_path(merged, field.path))
        return values

    def _render_config(base: dict, local: dict, problems: list, form: dict[str, str] | None = None,
                       saved: bool = False, status_code: int = 200) -> HTMLResponse:
        values = _form_values(base, local, form)
        defaults = {f.path: format_form_value(f, get_path(base, f.path))
                    for f in FIELDS if f.editable}
        local_paths = {f.path for f in FIELDS
                       if f.editable and get_path(local, f.path, _MISSING) is not _MISSING}
        groups: list[tuple[str, list]] = []
        for field in FIELDS:
            if not field.editable:
                continue
            section = _section_title(field.path)
            if not groups or groups[-1][0] != section:
                groups.append((section, []))
            groups[-1][1].append(field)
        template = _env.get_template("config.html")
        return HTMLResponse(template.render(
            groups=groups, values=values, defaults=defaults, local_paths=local_paths,
            problems=problems, saved=saved,
            save_action="/config/save", preview_action="/config/preview",
            reset_action="/config/reset",
        ), status_code=status_code)

    @app.get("/config", response_class=HTMLResponse)
    def config_page(request: Request):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        base, local = _read_files()
        saved = request.query_params.get("saved") == "1"
        return _render_config(base, local, problems=[], saved=saved)

    @app.post("/config/preview")
    async def config_preview(request: Request):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        if not _same_origin(request):
            return PlainTextResponse("Ungültige Herkunft", status_code=403)
        form = {key: value for key, value in (await request.form()).items()}
        base, local = _read_files()
        new_local, problems = apply_form(base, local, form)
        if any(problem.level == "error" for problem in problems):
            return _render_config(base, local, problems=problems, form=form, status_code=422)
        changes = diff_config(deep_merge(base, local), deep_merge(base, new_local))
        warnings = [problem for problem in problems if problem.level == "warning"]
        template = _env.get_template("config_preview.html")
        return HTMLResponse(template.render(changes=changes, warnings=warnings, config_action="/config"))

    @app.post("/config/save")
    async def config_save(request: Request):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        if not _same_origin(request):
            return PlainTextResponse("Ungültige Herkunft", status_code=403)
        form = {key: value for key, value in (await request.form()).items()}
        base, local = _read_files()
        new_local, problems = apply_form(base, local, form)
        if any(problem.level == "error" for problem in problems):
            return _render_config(base, local, problems=problems, form=form, status_code=422)
        if new_local != local:
            save_local(_local_path, new_local)
            app.state.cfg = Config.load(_base_path, local=_local_path)
        return RedirectResponse("/config?saved=1", status_code=303)

    @app.post("/config/reset")
    async def config_reset(request: Request):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        if not _same_origin(request):
            return PlainTextResponse("Ungültige Herkunft", status_code=403)
        path = str((await request.form()).get("path", ""))
        field = get_field(path)
        base, local = _read_files()
        if field is None or not field.editable:
            problem = Problem("error", path, "Dieser Schlüssel ist nicht editierbar.")
            return _render_config(base, local, problems=[problem], status_code=422)
        save_local(_local_path, remove_key(local, path))
        app.state.cfg = Config.load(_base_path, local=_local_path)
        return RedirectResponse("/config?saved=1", status_code=303)

    return app


_MISSING = object()


def _section_title(path: str) -> str:
    """Überschrift des Abschnitts aus SECTION_TITLES; sonst der Abschnittsname."""
    parts = path.split(".")
    for size in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:size])
        if candidate in SECTION_TITLES:
            return SECTION_TITLES[candidate]
    return parts[0] if len(parts) > 1 else "Allgemein"
