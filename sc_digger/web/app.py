"""FastAPI-App der Statusseite: HTTP-Basic geschützt, ohne API-Dokumentation.

Die Status- und Explorer-Routen sind rein lesend (GET). Schreibende Pfade gibt es beim
Feedback (``POST /api/feedback``, Issue #229) und beim Upload (``POST /api/upload-intake``,
Issue #230). Der Mix-Finder (``GET /mix``) und der On-Demand-URL-Scanner
(``POST /api/check-url``, Issue #231) lesen nur: sie öffnen die Track-DB über ``mode=ro``
und ändern weder State noch Sammlung – anders als ``main.run_link``, das dieselbe Auswahl
verschickt und als gesehen markiert.

Ist zusätzlich ein Pfad zur lokalen Override-Datei gesetzt (Issue #145), kommen die schreibenden
Editor-Routen unter `/config` dazu. Dann wird bei jeder Anfrage die Basis- und die lokale Datei
frisch gelesen; die versionierte `config.yaml` bleibt unangetastet.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlencode, urlparse

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..db import FEEDBACK_VALUES
from ..dj_export import Playlist, order_harmonic
from ..harmonic import find_mix_candidates
from ..models import Config
from ..pipeline import classify_download, dedupe, estimate_bpm
from ..schema import FIELDS, SECTION_DESCRIPTIONS, SECTION_TITLES, Problem, deep_merge, get_field
from ..soundcloud import SoundCloudClient, SoundCloudError
from .configedit import apply_form, diff_config, format_form_value, get_path, remove_key, save_local
from .database import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    _connect_readonly,
    _sc_id_from_path,
    _set_builder_entries,
    claim_job_for_worker,
    finish_job,
    get_collection_overview,
    heartbeat_job,
    load_tracks_for_set_builder,
    query_digest,
    query_store_items,
    query_tracks,
    save_track_feedback,
)
from .intake import process_uploaded_audio
from .status import collect_status

_TEMPLATES = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES)),
    autoescape=select_autoescape(("html", "xml")),
)
log = logging.getLogger(__name__)


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


def _payload_float(value: object) -> float | None:
    """JSON-Wert als float; None, leer oder ungültig -> None (kein Filter)."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _m3u8_safe(value: object) -> str:
    """Feld für eine M3U8-Zeile entschärfen: Zeilenumbrüche und Steuerzeichen entfernen.

    Artist und Titel stammen aus Dateien und fremden Quellen. Ein Zeilenumbruch im Namen
    würde sonst eine eigene M3U8-Anweisung erzeugen (Header-Injection).
    """
    return " ".join(str(value if value is not None else "").split())


def _m3u8_filename(name: str) -> str:
    """Unbedenklicher Dateiname für ``Content-Disposition`` (keine Quotes/Umbrüche)."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", _m3u8_safe(name)).strip("._-")
    return f"{stem or 'set'}.m3u8"


def _render_m3u8(playlist: Playlist, name: str) -> str:
    """Playlist als M3U8-Text mit vollständigen Pfaden und Segmentmarken.

    ``dj_export`` schreibt die Datei selbst; für die HTTP-Antwort wird der Text direkt
    gebraucht. Alle Felder laufen durch :func:`_m3u8_safe`, damit ein Titel mit
    Zeilenumbruch keine zusätzliche Anweisung einschleusen kann.
    """
    lines = ["#EXTM3U"]
    if name:
        lines.append(f"#PLAYLIST:{_m3u8_safe(name)}")
    transitions = iter(playlist.transitions)
    for pos, entry in enumerate(playlist.entries):
        if pos > 0:
            transition = next(transitions, None)
            if transition is not None and transition["typ"] == "schnitt":
                lines.append(f"# Segment {transition['segment']} (harmonisch fortlaufend)")
        seconds = int(entry.duration_s) if entry.duration_s else -1
        info = f"{entry.artist} - {entry.title}" if entry.artist else entry.title
        tag = (
            f"{entry.camelot or '--'} · {entry.bpm:.0f} BPM"
            if entry.bpm else f"{entry.camelot or '--'} · -- BPM"
        )
        lines.append(f"#EXTINF:{seconds},{_m3u8_safe(info)} [{_m3u8_safe(tag)}]")
        lines.append(str(entry.path))
    return "\n".join(lines) + "\n"


def _build_set_playlist(
    track_db_path: str | Path,
    *,
    bpm_min: float | None,
    bpm_max: float | None,
    status: str | None,
    track_ids: list[int] | None,
) -> Playlist:
    """Kandidaten lesen und harmonisch sortieren (``order_harmonic``).

    Mit ``track_ids`` wird bereits in SQL gefiltert, damit die Auswahl nicht am
    Standard-Limit hängt; ohne Auswahl gilt das Limit der lesenden Abfrage.
    """
    if track_ids is not None:
        entries = [
            entry
            for _, entry in _set_builder_entries(
                track_db_path,
                bpm_min=bpm_min,
                bpm_max=bpm_max,
                status=status,
                track_ids=track_ids,
                limit=MAX_LIMIT,
            )
        ]
    else:
        entries = load_tracks_for_set_builder(
            track_db_path,
            bpm_min=bpm_min,
            bpm_max=bpm_max,
            status=status,
            limit=DEFAULT_LIMIT,
        )
    return order_harmonic(entries)


class _ReadOnlyTrackDB:
    """Hülle, die dem Mix-Finder nur ``db.db`` (die ro-Verbindung) durchreicht.

    ``TrackDB.__init__`` legt fehlende Dateien und das Schema an (Schreibzugriff), deshalb
    darf die lesende GET-Route keine echte ``TrackDB`` benutzen. ``find_mix_candidates`` braucht
    lediglich ein Objekt mit einem ``db``-Attribut (``sqlite3.Connection``).
    """

    def __init__(self, connection: sqlite3.Connection):
        self.db = connection


def _known_sc_statuses(cfg: Config) -> dict[int, str]:
    """SoundCloud-ID -> Status, rein lesend aus Track- und State-DB.

    Sammlung/Inbox/abgelehnt kommen aus dem Status der Track-DB (``[<sc_id>]`` im Dateinamen),
    „gesehen“ aus der seen-Tabelle der State-DB. Fehlt eine Datei oder Tabelle, wird sie
    übersprungen; der Rest bleibt nutzbar.
    """
    statuses: dict[int, str] = {}
    labels = {"archive": "in Sammlung", "inbox": "in Inbox", "rejected": "abgelehnt"}
    try:
        with closing(_connect_readonly(cfg["state"]["track_db_path"])) as db:
            for path, status in db.execute("SELECT path, status FROM tracks"):
                sc_id = _sc_id_from_path(path)
                if sc_id is not None and status in labels:
                    statuses[sc_id] = labels[status]
    except sqlite3.Error:
        pass
    try:
        with closing(_connect_readonly(cfg["state"]["db_path"])) as db:
            for (sc_id,) in db.execute("SELECT id FROM seen"):
                statuses.setdefault(int(sc_id), "gesehen")
    except sqlite3.Error:
        pass
    return statuses


def _scan_url(cfg: Config, url: str) -> dict:
    """On-Demand-Check eines SoundCloud-Links für den Browser.

    Playlist-Link -> enthaltene Tracks, Track-Link -> Track-Station (wie ``main.run_link``).
    Rein lesend: kein Download, kein Telegram, kein Eintrag in der seen-DB.
    """
    sc = SoundCloudClient()
    kind = sc.resolve_kind(url)
    if kind == "playlist":
        label, tracks = sc.playlist_tracks(url, limit=200)
        source = f"Playlist: {label}"
    elif kind == "track":
        label, tracks = sc.track_station(url, limit=50)
        source = f"Station zu: {label}"
    else:
        raise SoundCloudError("Das ist weder ein Track- noch ein Playlist-Link.")

    statuses = _known_sc_statuses(cfg)
    items = []
    for track in dedupe(tracks):
        track.bpm = estimate_bpm(track)
        classify_download(track)
        items.append({
            "id": track.id,
            "artist": track.artist,
            "title": track.title,
            "url": track.url,
            "bpm": track.bpm,
            "duration_ms": track.duration_ms,
            "download_kind": track.download_kind.value,
            "download_link": track.download_link,
            "status": statuses.get(track.id, "neu"),
            # Engagement-Kennzahlen für die Meta-Zeile im Mix-Dashboard (Issue #257),
            # gleiche Werte wie in der Telegram-Darstellung.
            "plays": track.plays,
            "likes": track.likes,
            "reposts": track.reposts,
            "comments": track.comments,
            "like_ratio": track.like_ratio,
        })
    return {"source": source, "kind": kind, "total": len(items), "tracks": items}


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

    # Job-Queue für externe GPU-Worker (Issue #330): claimen, Lebenszeichen und Ergebnis
    # melden über HTTP, damit die Workstation die Track-DB nicht per SMB/NFS einbinden muss.
    # Keine Origin-Prüfung: die Clients sind Skripte auf einer anderen Maschine, keine
    # Browser-Formulare; geschützt ist die API über dieselbe Basic-Anmeldung wie der Rest.
    @app.post("/api/jobs/claim")
    async def api_jobs_claim(request: Request):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        raw = await request.body()
        payload: dict = {}
        if raw.strip():
            try:
                payload = json.loads(raw)
            except Exception:
                return JSONResponse({"ok": False, "error": "Ungültiges JSON"}, status_code=400)
            if not isinstance(payload, dict):
                return JSONResponse({"ok": False, "error": "Ungültiger Payload"}, status_code=400)
        job_type = payload.get("job_type")
        job = claim_job_for_worker(
            cfg["state"]["track_db_path"], str(job_type) if job_type else None
        )
        return JSONResponse({"ok": True, "job": job})

    @app.post("/api/jobs/{job_id}/heartbeat")
    def api_jobs_heartbeat(request: Request, job_id: int):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        if heartbeat_job(cfg["state"]["track_db_path"], job_id):
            return JSONResponse({"ok": True})
        return JSONResponse(
            {"ok": False, "error": "Job nicht gefunden oder nicht im Status running"},
            status_code=404,
        )

    @app.post("/api/jobs/{job_id}/complete")
    async def api_jobs_complete(request: Request, job_id: int):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Ungültiges JSON"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"ok": False, "error": "Ungültiger Payload"}, status_code=400)
        status = payload.get("status")
        # Der Status entscheidet über die Job-Tabelle; nur die beiden Endzustände sind gültig.
        if status not in ("completed", "failed"):
            return JSONResponse(
                {"ok": False, "error": "status muss completed oder failed sein"},
                status_code=422,
            )
        done = finish_job(
            cfg["state"]["track_db_path"],
            job_id,
            status,
            result=payload.get("result"),
            error=payload.get("error"),
        )
        if done:
            return JSONResponse({"ok": True})
        return JSONResponse({"ok": False, "error": "Job nicht gefunden"}, status_code=404)

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
            breakdown_ratio_max=_query_float(params.get("breakdown_max")),
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
            "breakdown_max": params.get("breakdown_max") or "",
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
            breakdown_ratio_max=_query_float(filters["breakdown_max"]),
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

    @app.get("/mix", response_class=HTMLResponse)
    def mix_page(request: Request):
        # Harmonic Mix Finder (Issue #231): dieselbe Auswahl wie der Telegram-Befehl /mix,
        # aber als Formular. Nur lesend – die Track-DB wird über mode=ro geöffnet, damit ein
        # GET keine Datei und kein Schema anlegt.
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        params = request.query_params
        key = (params.get("key") or "").strip()
        bpm_raw = (params.get("bpm") or "").strip()
        tol_raw = (params.get("tol") or "").strip()
        bpm = _query_float(bpm_raw)
        tolerance = _query_float(tol_raw)
        candidates: list = []
        error: str | None = None
        searched = bool(key)
        if searched:
            if bpm is None:
                error = "Bitte eine Ziel-BPM angeben."
            else:
                if tolerance is None or tolerance <= 0:
                    tolerance = 3.0
                try:
                    with closing(_connect_readonly(cfg["state"]["track_db_path"])) as db:
                        candidates = find_mix_candidates(
                            _ReadOnlyTrackDB(db), key, bpm,
                            bpm_tolerance=tolerance, limit=30,
                        )
                except ValueError as exc:
                    error = str(exc)
                except sqlite3.Error:
                    error = "Track-Datenbank nicht lesbar."
        template = _env.get_template("mix.html")
        return HTMLResponse(template.render(
            key=key, bpm=bpm_raw, tol=tol_raw,
            candidates=candidates, error=error, searched=searched,
        ))

    # Harmonischer Set-Builder (Issue #340): dieselbe Graph-Optimierung wie
    # `python -m sc_digger.dj_export`, aber auf Abruf aus der Track-DB. Beide Routen
    # lesen nur; die Playlist landet als M3U8 im Browser, nicht auf der Sammlung.
    @app.post("/api/set-builder")
    async def api_set_builder(request: Request):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Ungültiges JSON"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"ok": False, "error": "Ungültiger Payload"}, status_code=400)

        raw_ids = payload.get("track_ids")
        if raw_ids is None:
            track_ids = None
        elif isinstance(raw_ids, list):
            try:
                track_ids = [int(item) for item in raw_ids]
            except (TypeError, ValueError):
                return JSONResponse(
                    {"ok": False, "error": "track_ids müssen Zahlen sein"}, status_code=400
                )
        else:
            return JSONResponse(
                {"ok": False, "error": "track_ids muss eine Liste sein"}, status_code=400
            )

        playlist = _build_set_playlist(
            cfg["state"]["track_db_path"],
            bpm_min=_payload_float(payload.get("bpm_min")),
            bpm_max=_payload_float(payload.get("bpm_max")),
            status=str(payload.get("status") or "").strip() or None,
            track_ids=track_ids,
        )
        return JSONResponse({
            "ok": True,
            "total_tracks": len(playlist.entries),
            "segments": playlist.segments,
            "playlist": [
                {
                    "pos": pos,
                    "artist": entry.artist,
                    "title": entry.title,
                    "bpm": entry.bpm,
                    "camelot": entry.camelot,
                    "path": str(entry.path),
                }
                for pos, entry in enumerate(playlist.entries, 1)
            ],
            "transitions": [
                {
                    key: transition[key]
                    for key in ("typ", "von", "nach", "harmonisch",
                                "kosten", "dbpm", "warnung")
                }
                for transition in playlist.transitions
            ],
        })

    @app.get("/api/set-builder/m3u8")
    def api_set_builder_m3u8(request: Request):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        params = request.query_params
        name = (params.get("name") or "set").strip() or "set"
        raw_ids = (params.get("track_ids") or "").strip()
        track_ids: list[int] | None = None
        if raw_ids:
            try:
                track_ids = [int(part) for part in raw_ids.split(",") if part.strip()]
            except ValueError:
                return PlainTextResponse(
                    "track_ids müssen Zahlen sein", status_code=400
                )
        playlist = _build_set_playlist(
            cfg["state"]["track_db_path"],
            bpm_min=_query_float(params.get("bpm_min")),
            bpm_max=_query_float(params.get("bpm_max")),
            status=(params.get("status") or "").strip() or None,
            track_ids=track_ids,
        )
        return Response(
            content=_render_m3u8(playlist, name),
            media_type="application/x-mpegurl",
            headers={
                "Content-Disposition": f'attachment; filename="{_m3u8_filename(name)}"'
            },
        )

    @app.post("/api/check-url")
    async def api_check_url(request: Request):
        # On-Demand-URL-Scanner (Issue #231): beliebiger SoundCloud-Link -> Trefferliste als JSON.
        # Origin-Prüfung wie bei den übrigen POST-Routen, obwohl hier nichts geschrieben wird.
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        if not _same_origin(request):
            return JSONResponse({"ok": False, "error": "Ungültige Herkunft"}, status_code=403)
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Ungültiges JSON"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"ok": False, "error": "Ungültiger Payload"}, status_code=400)
        url = str(payload.get("url") or "").strip()
        if not url:
            return JSONResponse(
                {"ok": False, "error": "Kein SoundCloud-Link angegeben"}, status_code=400
            )
        try:
            result = _scan_url(cfg, url)
        except SoundCloudError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        except Exception as exc:  # Netz/Client-ID/…: dem Browser eine Meldung geben (Regel 7)
            log.warning("URL-Scanner fehlgeschlagen: %s", exc)
            return JSONResponse(
                {"ok": False, "error": "SoundCloud-Abfrage fehlgeschlagen"}, status_code=502
            )
        return JSONResponse({"ok": True, **result})

    @app.get("/api/digest")
    def api_digest(request: Request):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        params = request.query_params
        result = query_digest(
            cfg["state"]["db_path"],
            cfg["state"]["track_db_path"],
            inbox_dir=(cfg.raw.get("download") or {}).get("inbox_dir"),
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
            inbox_dir=(cfg.raw.get("download") or {}).get("inbox_dir"),
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

    # Kaufempfehlungen aus store_items (Issue #338): dieselbe rein lesende Abfrage für
    # JSON-API und Seite, Bewertungen laufen über das bestehende POST /api/feedback.
    @app.get("/api/kaufliste")
    def api_kaufliste(request: Request):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        params = request.query_params
        result = query_store_items(
            cfg["state"]["track_db_path"],
            search=params.get("q") or None,
            feedback=params.get("feedback") or None,
            limit=_query_int(params.get("limit"), DEFAULT_LIMIT),
            offset=_query_int(params.get("offset"), 0),
        )
        return JSONResponse(result.to_dict())

    @app.get("/kaufliste", response_class=HTMLResponse)
    def kaufliste_page(request: Request):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        params = request.query_params
        filters = {
            "q": params.get("q") or "",
            "feedback": params.get("feedback") or "",
        }
        page_size = max(1, min(_query_int(params.get("limit"), DEFAULT_LIMIT), MAX_LIMIT))
        offset = max(0, _query_int(params.get("offset"), 0))
        result = query_store_items(
            cfg["state"]["track_db_path"],
            search=filters["q"] or None,
            feedback=filters["feedback"] or None,
            limit=page_size,
            offset=offset,
        )

        def page_url(new_offset: int) -> str:
            """Link auf eine andere Seite, aktive Filter bleiben erhalten."""
            query = {key: value for key, value in filters.items() if value}
            query["limit"] = str(page_size)
            if new_offset > 0:
                query["offset"] = str(new_offset)
            return "/kaufliste?" + urlencode(query)

        total_pages = max(1, (result.total + page_size - 1) // page_size)
        template = _env.get_template("kaufliste.html")
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

    # Interaktive Bewertung aus dem Web (Issue #229): derselbe Speicherpfad wie die
    # Telegram-Buttons (bot.py), damit Digest-Historie, /mix und Statistik dasselbe Feedback
    # sehen. Origin-Prüfung wie beim Konfigeditor, weil die Route schreibt (CSRF).
    @app.post("/api/feedback")
    async def api_feedback(request: Request):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        if not _same_origin(request):
            return JSONResponse({"ok": False, "error": "Ungültige Herkunft"}, status_code=403)
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Ungültiges JSON"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"ok": False, "error": "Ungültiger Payload"}, status_code=400)
        value = payload.get("value")
        sc_id = payload.get("sc_id")
        if value not in FEEDBACK_VALUES or isinstance(sc_id, bool):
            return JSONResponse(
                {"ok": False, "error": "value muss like/dislike/later sein, sc_id eine Zahl"},
                status_code=400,
            )
        try:
            sc_id_int = int(sc_id)
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "Ungültige sc_id"}, status_code=400)
        if not save_track_feedback(cfg["state"]["track_db_path"], sc_id_int, str(value)):
            return JSONResponse(
                {"ok": False, "sc_id": sc_id_int, "value": str(value)}, status_code=500
            )
        return JSONResponse({"ok": True, "sc_id": sc_id_int, "value": str(value)})

    # Datei-Upload in den Eingangsordner (Issue #230, Gate-Assistent): Stephan legt Dateien
    # ab, die er nach einem Gate (Hypeddit, Droploud, …) selbst am PC geholt hat. Der Upload
    # umgeht das Gate nicht, er ersetzt nur das Kopieren in den Eingangsordner; die Prüfung
    # läuft danach wie beim täglichen Lauf über main.finish_file. Origin-Prüfung wie beim
    # Feedback, weil die Route schreibt (CSRF).
    @app.post("/api/upload-intake")
    async def api_upload_intake(request: Request):
        if not _check_auth(request, password, allow_anonymous):
            return _unauthorized()
        if not _same_origin(request):
            return JSONResponse({"ok": False, "error": "Ungültige Herkunft"}, status_code=403)
        try:
            form = await request.form()
        except Exception:
            return JSONResponse({"ok": False, "error": "Kein Multipart-Formular"}, status_code=400)
        upload = form.get("file")
        # Ein Textfeld namens „file“ ist kein Datei-Upload; nur UploadFile hat .file.
        if upload is None or not hasattr(upload, "file"):
            return JSONResponse(
                {"ok": False, "error": "Feld 'file' mit einer Audiodatei fehlt"}, status_code=400
            )
        result = process_uploaded_audio(upload.file, upload.filename or "", cfg)
        # 200 nur, wenn die Datei wirklich in der Inbox liegt; abgelehnt/fehlgeschlagen ist 422,
        # damit ein Upload-Skript den Unterschied sieht, ohne den Text zu lesen.
        return JSONResponse(asdict(result), status_code=200 if result.ok else 422)

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
            problems=problems, saved=saved, section_descriptions=SECTION_DESCRIPTIONS,
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
