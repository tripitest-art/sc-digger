"""FastAPI-App der Statusseite: HTTP-Basic geschützt, ohne API-Dokumentation.

Die Status- und Explorer-Routen sind rein lesend (GET). Schreibende Pfade gibt es beim
Feedback (``POST /api/feedback``, Issue #229), Matcher-Korrekturen (``POST /api/match-override``,
Issue #498) und beim Upload (``POST /api/upload-intake``,
Issue #230). Der Mix-Finder (``GET /mix``) und der On-Demand-URL-Scanner
(``POST /api/check-url``, Issue #231) lesen nur: sie öffnen die Track-DB über ``mode=ro``
und ändern weder State noch Sammlung – anders als ``main.run_link``, das dieselbe Auswahl
verschickt und als gesehen markiert.

Ist zusätzlich ein Pfad zur lokalen Override-Datei gesetzt (Issue #145), kommen die schreibenden
Editor-Routen unter `/config` dazu. Dann wird bei jeder Anfrage der Editor-Seite die Basis- und die
lokale Datei frisch gelesen; nach dem Speichern ersetzt `app.state.cfg` den Stand, und alle anderen
Routen (Digest, Tracks, Vorhören, Status) lesen ihn bei jeder Anfrage von dort. Die versionierte
`config.yaml` bleibt unangetastet.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
import math
import sqlite3
import xml.etree.ElementTree as ET
from contextlib import closing
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.concurrency import run_in_threadpool

from .. import rekordbox
from ..db import FEEDBACK_VALUES, JobType, TrackDB
from ..dj_export import Playlist, order_harmonic
from ..embeddings import apply_embedding_result
from ..harmonic import find_mix_candidates
from ..models import Config
from ..pipeline import classify_download, dedupe, estimate_bpm
from ..redact import redact
from ..schema import FIELDS, SECTION_DESCRIPTIONS, SECTION_TITLES, Problem, deep_merge, get_field
from ..soundcloud import SoundCloudClient, SoundCloudError
from .common import (
    LOGIN_LOCKOUT_SECONDS,
    LOGIN_MAX_FAILURES,
    LOGIN_WINDOW_SECONDS,
    PUBLIC_PATHS,
    FailureTracker,
    _AUDIO_BLOCK_SIZE,
    _FALSES,
    _ReadOnlyTrackDB,
    _SCAN_NOT_TRACK_OR_PLAYLIST,
    _ScanLinkError,
    _TEMPLATES,
    _TRUES,
    _UPLOAD_OVERHEAD_BYTES,
    _audio_response,
    _check_auth,
    _env,
    _iter_file_range,
    _load_yaml,
    _m3u8_filename,
    _m3u8_safe,
    _payload_float,
    _playable_roots,
    _query_bool,
    _query_float,
    _query_int,
    _render,
    _require_json_content_type,
    _same_origin,
    _unauthorized,
    _xml_filename,
    client_key,
)
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
    query_rejected_tracks,
    query_store_items,
    query_watched_tracks,
    retry_quarantined_track,
    save_match_override,
    save_track_feedback,
)
from .intake import MAX_UPLOAD_BYTES, process_uploaded_audio
from .ops import collect_ops
from .routes import tracks as tracks_routes
from .routes.tracks import FILTER_LABELS, active_filter_chips, match_url
from .status import change_badge, collect_status

# Ob der Konfigeditor in der aktuellen Anfrage verfuegbar ist (die Umgebung _env ist modulweit,
# die Apps sind es nicht); base.html zeigt das Zahnrad nur dann, sonst laege es auf einem 404.
_config_enabled: ContextVar[bool] = ContextVar("sc_digger_config_enabled", default=False)
_env.globals["config_enabled"] = lambda: _config_enabled.get()

log = logging.getLogger(__name__)


def render_set_xml(
    playlist: Playlist,
    name: str = "set",
    path_map: dict[str, str] | None = None,
) -> str:
    """Erzeugt eine Rekordbox-XML (UTF-8) für eine berechnete Set-Builder-Playlist.

    - Wurzel: `<DJ_PLAYLISTS Version="1.0.0">` mit `<PRODUCT Name="rekordbox" Version="6.0.0" Company="Pioneer DJ" />`.
    - `<COLLECTION Entries="{N}">`: für jeden Eintrag `pos, entry` in `enumerate(playlist.entries, 1)` ein `<TRACK>`-Element mit:
      - `TrackID`: `str(pos)`
      - `Name`: `entry.title or Path(entry.path).stem`
      - `Artist`: `entry.artist or ""`
      - `TotalTime`: `str(int(round(entry.duration_s or 0)))`
      - `Tonality`: `entry.camelot or ""`
      - `AverageBpm`: `f"{entry.bpm:.2f}"` (nur wenn `entry.bpm is not None`)
      - `Location`: falls `path_map` angegeben ist und `rekordbox.laptop_location(Path(entry.path), path_map)` einen Wert liefert, diesen verwenden; sonst Fallback über `rekordbox.laptop_location(Path(entry.path), {"/": ""})` bzw. `"file://localhost/" + Path(entry.path).as_posix().lstrip("/")`, sodass kein Track aus der Kette verloren geht.
    - `<PLAYLISTS>`: Wurzelknoten `<NODE Type="0" Name="ROOT" Count="1">`, darunter Ordner `<NODE Type="0" Name="sc-digger" Count="1">`, darunter Playlist-Knoten `<NODE Type="1" Name="{bereinigter_name}" KeyType="0" Entries="{N}">` mit `<TRACK Key="{pos}" />` in exakt der Reihenfolge von `playlist.entries`. Ist `name` leer oder nur Whitespace, wird `"set"` verwendet.
    - Verändert keine Dateien auf der Festplatte und keine Datenbank.
    """
    clean_name = (name or "").strip() or "set"
    root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
    ET.SubElement(
        root,
        "PRODUCT",
        {"Name": "rekordbox", "Version": "6.0.0", "Company": "Pioneer DJ"},
    )

    n_entries = str(len(playlist.entries))
    collection = ET.SubElement(root, "COLLECTION", {"Entries": n_entries})

    for pos, entry in enumerate(playlist.entries, 1):
        pos_str = str(pos)
        entry_path = Path(entry.path)
        track_name = entry.title or entry_path.stem
        artist = entry.artist or ""
        total_time = str(int(round(entry.duration_s or 0)))
        tonality = entry.camelot or ""

        loc: str | None = None
        if path_map:
            loc = rekordbox.laptop_location(entry_path, path_map)
        if not loc:
            loc = rekordbox.laptop_location(entry_path, {"/": ""})
        if not loc:
            loc = "file://localhost/" + entry_path.as_posix().lstrip("/")

        attr = {
            "TrackID": pos_str,
            "Name": track_name,
            "Artist": artist,
            "TotalTime": total_time,
            "Tonality": tonality,
            "Location": loc,
        }
        if entry.bpm is not None:
            attr["AverageBpm"] = f"{entry.bpm:.2f}"

        ET.SubElement(collection, "TRACK", attr)

    playlists_elem = ET.SubElement(root, "PLAYLISTS")
    root_node = ET.SubElement(
        playlists_elem, "NODE", {"Type": "0", "Name": "ROOT", "Count": "1"}
    )
    folder_node = ET.SubElement(
        root_node, "NODE", {"Type": "0", "Name": "sc-digger", "Count": "1"}
    )
    playlist_node = ET.SubElement(
        folder_node,
        "NODE",
        {"Type": "1", "Name": clean_name, "KeyType": "0", "Entries": n_entries},
    )
    for pos in range(1, len(playlist.entries) + 1):
        ET.SubElement(playlist_node, "TRACK", {"Key": str(pos)})

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


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
        lines.append(_m3u8_safe(entry.path))
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
        raise _ScanLinkError(_SCAN_NOT_TRACK_OR_PLAYLIST)

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


def sync_rekordbox_xml(cfg: Config) -> tuple[int, dict]:
    """Schreibt die Rekordbox-XML der Inbox neu und liefert (HTTP-Status, JSON-Body).

    - cfg.raw["rekordbox"] fehlt, ist kein dict oder xml_enabled ist falsch:
      (409, {"ok": False, "error": "Rekordbox-XML ist abgeschaltet (rekordbox.xml_enabled)."}),
      es wird nichts geschrieben.
    - rekordbox.write_rekordbox_xml(cfg) liefert None:
      (500, {"ok": False, "error": "Rekordbox-XML konnte nicht geschrieben werden, siehe Container-Log."}).
    - Sonst: Zahl der Tracks aus dem Attribut Entries des Knotens COLLECTION der geschriebenen
      Datei lesen und (200, {"ok": True, "entries": <int>}) zurückgeben.
    - Jede Ausnahme (auch beim Lesen der Datei): log.exception, dieselbe 500er-Antwort.
      Wirft nie. Der Dateipfad steht in keiner Antwort.
    """
    try:
        rb_cfg = cfg.raw.get("rekordbox") if isinstance(cfg.raw, dict) else None
        if not rb_cfg or not isinstance(rb_cfg, dict) or not rb_cfg.get("xml_enabled"):
            return 409, {
                "ok": False,
                "error": "Rekordbox-XML ist abgeschaltet (rekordbox.xml_enabled).",
            }

        xml_path = rekordbox.write_rekordbox_xml(cfg)
        if xml_path is None:
            return 500, {
                "ok": False,
                "error": "Rekordbox-XML konnte nicht geschrieben werden, siehe Container-Log.",
            }

        root = ET.parse(xml_path).getroot()
        collection = root.find("COLLECTION")
        n = collection.get("Entries") if collection is not None else None
        return 200, {"ok": True, "entries": int(n)}
    except Exception:
        log.exception("Fehler beim Neuschreiben der Rekordbox-XML")
        return 500, {
            "ok": False,
            "error": "Rekordbox-XML konnte nicht geschrieben werden, siehe Container-Log.",
        }


def create_app(cfg: Config, *, password: str | None, allow_anonymous: bool = False,
               base_path: Path | None = None, local_path: Path | None = None,
               login_limit: FailureTracker | None = None,
               trust_proxy: bool = False) -> FastAPI:
    """Erzeugt die Status-App.

    password leer/None und allow_anonymous False: ValueError (nie versehentlich offen starten).
    Keine API-Dokumentation ausliefern. app.state.cfg = cfg.
    Nur wenn base_path UND local_path gesetzt sind, gibt es die Editor-Routen; sonst 404.
    login_limit: ohne Tracker keine Sperre (401 wie bisher); mit Tracker 429 nach zu vielen
    Fehlversuchen. trust_proxy wertet für die Sperre die letzte Station aus X-Forwarded-For aus.
    """
    if not password and not allow_anonymous:
        raise ValueError("SC_DIGGER_WEB_PASSWORD ist nicht gesetzt; Start abgelehnt")

    # Editor-Routen gibt es nur mit beiden Pfaden; status.html verlinkt sie nur dann.
    editor_available = base_path is not None and local_path is not None

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cfg = cfg
    app.state.password = password
    app.state.allow_anonymous = allow_anonymous
    app.state.login_limit = login_limit

    @app.middleware("http")
    async def mark_config_enabled(request: Request, call_next):
        _config_enabled.set(editor_available)
        return await call_next(request)

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        key = client_key(request, trust_proxy=trust_proxy) if login_limit else ""
        if login_limit is not None:
            # Die Sperre gilt vor der Passwortprüfung, sonst wäre sie mit dem richtigen
            # Passwort nach dem Durchprobieren sofort umgangen.
            locked = login_limit.is_locked(key)
            if locked > 0:
                return PlainTextResponse(
                    "Zu viele Fehlversuche. Bitte später erneut versuchen.",
                    status_code=429,
                    headers={"Retry-After": str(max(1, math.ceil(locked)))},
                )
        if not _check_auth(request, password, allow_anonymous):
            if login_limit is not None:
                login_limit.fail(key)
            return _unauthorized()
        if login_limit is not None:
            login_limit.reset(key)
        return await call_next(request)

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        snap = collect_status(app.state.cfg)
        overview = get_collection_overview(app.state.cfg["state"]["track_db_path"])
        ops = collect_ops(app.state.cfg)
        template = _env.get_template("status.html")
        return HTMLResponse(template.render(
            snap=snap, overview=overview, editor_available=editor_available, ops=ops
        ))

    @app.get("/api/status")
    def api_status(request: Request):
        return JSONResponse(collect_status(app.state.cfg).to_dict())

    # Schreibt die Inbox-XML neu wie der Bot-Befehl /sync_xml (Issue #495). Normale def-Route,
    # damit FastAPI sie im Threadpool ausführt und die Event-Loop nicht blockiert. Content-Type-
    # und Origin-Prüfung wie /api/jobs/claim; ein Aufruf ohne Body ist erlaubt.
    @app.post("/api/rekordbox/sync")
    def api_rekordbox_sync(request: Request):
        ct_err = _require_json_content_type(request, allow_empty_body=True)
        if ct_err is not None:
            return ct_err
        status, body = sync_rekordbox_xml(app.state.cfg)
        return JSONResponse(body, status_code=status)

    # Job-Queue für externe GPU-Worker (Issue #330): claimen, Lebenszeichen und Ergebnis
    # melden über HTTP, damit die Workstation die Track-DB nicht per SMB/NFS einbinden muss.
    # Gegen Cross-Origin Simple Requests aus fremden Browsern (CSRF) erzwingen die Endpunkte
    # Content-Type: application/json (Issue #415); fremde Origins werden abgewiesen.
    @app.post("/api/jobs/claim")
    async def api_jobs_claim(request: Request):
        ct_err = _require_json_content_type(request)
        if ct_err is not None:
            return ct_err
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
            app.state.cfg["state"]["track_db_path"], str(job_type) if job_type else None
        )
        return JSONResponse({"ok": True, "job": job})

    @app.post("/api/jobs/{job_id}/heartbeat")
    def api_jobs_heartbeat(request: Request, job_id: int):
        ct_err = _require_json_content_type(request, allow_empty_body=True)
        if ct_err is not None:
            return ct_err
        if heartbeat_job(app.state.cfg["state"]["track_db_path"], job_id):
            return JSONResponse({"ok": True})
        return JSONResponse(
            {"ok": False, "error": "Job nicht gefunden oder nicht im Status running"},
            status_code=404,
        )

    @app.post("/api/jobs/{job_id}/complete")
    async def api_jobs_complete(request: Request, job_id: int):
        ct_err = _require_json_content_type(request)
        if ct_err is not None:
            return ct_err
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
            app.state.cfg["state"]["track_db_path"],
            job_id,
            status,
            result=payload.get("result"),
            error=payload.get("error"),
            inbox_dir=(app.state.cfg.raw.get("download") or {}).get("inbox_dir"),
        )
        if done:
            if status == "completed":
                with TrackDB(app.state.cfg["state"]["track_db_path"]) as db:
                    job = db.get_job_by_id(job_id)
                    if job is not None and (job.job_type == JobType.EMBEDDING or job.job_type == "embedding"):
                        apply_embedding_result(db, job_id, payload.get("result") or {})
            return JSONResponse({"ok": True})
        return JSONResponse({"ok": False, "error": "Job nicht gefunden"}, status_code=404)

    @app.get("/api/jobs/{job_id}/audio")
    def api_jobs_audio(request: Request, job_id: int):
        """Audiodatei zum Job, damit der GPU-Worker sie lokal analysieren kann.

        Gleiche Wurzeln und derselbe Schutz wie /api/audio/{track_id}: nur lesend,
        404 bei unbekanntem Job, fehlendem Track oder Datei außerhalb von Inbox/Sammlung.
        """
        with TrackDB(app.state.cfg["state"]["track_db_path"]) as db:
            job = db.get_job_by_id(job_id)
            track = db.get_track_by_id(job.track_id) if job is not None else None
        if track is None:
            return PlainTextResponse("Track nicht gefunden", status_code=404)
        return _audio_response(request, track.path, app.state.cfg)

    # Rein lesende Track-Abfragen für den Explorer (Teil 1 des Dashboards): nur die
    # Track-DB, ausschließlich `mode=ro`, keine Schreibpfade.
    @app.get("/api/database/overview")
    def api_database_overview(request: Request):
        return JSONResponse(get_collection_overview(app.state.cfg["state"]["track_db_path"]).to_dict())

    tracks_routes.register(app)

    @app.get("/mix", response_class=HTMLResponse)
    def mix_page(request: Request):
        # Harmonic Mix Finder (Issue #231): dieselbe Auswahl wie der Telegram-Befehl /mix,
        # aber als Formular. Nur lesend – die Track-DB wird über mode=ro geöffnet, damit ein
        # GET keine Datei und kein Schema anlegt.
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
                    with closing(_connect_readonly(app.state.cfg["state"]["track_db_path"])) as db:
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
            app.state.cfg["state"]["track_db_path"],
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
            app.state.cfg["state"]["track_db_path"],
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

    @app.post("/api/mix/export-xml")
    async def api_mix_export_xml(request: Request) -> Response:
        """Nimmt denselben JSON-Payload wie POST /api/set-builder entgegen:
        {"bpm_min": ..., "bpm_max": ..., "status": ..., "track_ids": [...], "name": "set"}.

        - Erfordert HTTP-Basic-Auth wie alle anderen Dashboard-Routen.
        - Ungültiges JSON oder Payload ist kein dict -> HTTP 400 mit JSON {"ok": False, "error": ...}.
        - `track_ids` angegeben, aber keine Liste aus ganzen Zahlen -> HTTP 400 mit JSON {"ok": False, "error": ...}.
        - Berechnet die Playlist über `_build_set_playlist(...)` (rein lesend).
        - Liest `path_map` optional aus `app.state.cfg.raw.get("rekordbox", {}).get("path_map")` (falls dict, sonst `{}`).
        - Antwortet mit HTTP 200, `media_type="application/xml"`, Header `Content-Disposition: attachment; filename="<bereinigt>.xml"` und dem von `render_set_xml` erzeugten XML-String.
        """
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
            if not all(isinstance(item, int) and not isinstance(item, bool) for item in raw_ids):
                return JSONResponse(
                    {"ok": False, "error": "track_ids müssen ganze Zahlen sein"}, status_code=400
                )
            track_ids = [int(item) for item in raw_ids]
        else:
            return JSONResponse(
                {"ok": False, "error": "track_ids muss eine Liste sein"}, status_code=400
            )

        name = str(payload.get("name") or "set").strip() or "set"
        filename = _xml_filename(name)

        playlist = _build_set_playlist(
            app.state.cfg["state"]["track_db_path"],
            bpm_min=_payload_float(payload.get("bpm_min")),
            bpm_max=_payload_float(payload.get("bpm_max")),
            status=str(payload.get("status") or "").strip() or None,
            track_ids=track_ids,
        )

        path_map: dict[str, str] = {}
        cfg_raw = getattr(app.state.cfg, "raw", None)
        if isinstance(cfg_raw, dict):
            rb_cfg = cfg_raw.get("rekordbox")
            if isinstance(rb_cfg, dict):
                pm = rb_cfg.get("path_map")
                if isinstance(pm, dict):
                    path_map = pm

        xml_content = render_set_xml(playlist, name=name, path_map=path_map)
        return Response(
            content=xml_content,
            media_type="application/xml",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            },
        )

    @app.post("/api/check-url")
    async def api_check_url(request: Request):
        # On-Demand-URL-Scanner (Issue #231): beliebiger SoundCloud-Link -> Trefferliste als JSON.
        # Origin-Prüfung wie bei den übrigen POST-Routen, obwohl hier nichts geschrieben wird.
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
            # Threadpool: _scan_url macht mehrere blockierende Netzwerkaufrufe, die sonst die
            # Event-Loop (und damit die ganze App) anhalten.
            result = await run_in_threadpool(_scan_url, app.state.cfg, url)
        except _ScanLinkError:
            return JSONResponse(
                {"ok": False, "error": _SCAN_NOT_TRACK_OR_PLAYLIST}, status_code=422
            )
        except Exception as exc:  # SoundCloud-Fehler, Netz, Client-ID: feste Meldung (Regel 7);
            # Ausnahmetexte enthalten URLs und gehören nur geschwärzt ins Log.
            log.warning("URL-Scanner fehlgeschlagen: %s", redact(exc))
            return JSONResponse(
                {"ok": False, "error": "SoundCloud-Abfrage fehlgeschlagen"}, status_code=502
            )
        return JSONResponse({"ok": True, **result})

    @app.get("/api/digest")
    def api_digest(request: Request):
        params = request.query_params
        sort = params.get("sort") or None
        direction = (params.get("dir") or "asc").lower()
        result = query_digest(
            app.state.cfg["state"]["db_path"],
            app.state.cfg["state"]["track_db_path"],
            inbox_dir=(app.state.cfg.raw.get("download") or {}).get("inbox_dir"),
            search=params.get("q") or "",
            status_filter=params.get("status") or "",
            download_kind=params.get("download_kind") or "",
            feedback=params.get("feedback") or "",
            sort=sort,
            descending=(direction == "desc"),
            limit=_query_int(params.get("limit"), DEFAULT_LIMIT),
            offset=_query_int(params.get("offset"), 0),
        )
        return JSONResponse(result.to_dict())

    @app.get("/digest", response_class=HTMLResponse)
    def digest_page(request: Request):
        params = request.query_params
        filters = {
            "q": params.get("q") or "",
            "status": params.get("status") or "",
            "download_kind": params.get("download_kind") or "",
            "feedback": params.get("feedback") or "",
        }
        raw_limit = params.get("limit")
        limit_val = DEFAULT_LIMIT if raw_limit == "" else _query_int(raw_limit, DEFAULT_LIMIT)
        page_size = max(1, min(limit_val, MAX_LIMIT))
        offset = max(0, _query_int(params.get("offset"), 0))
        sort = params.get("sort") or None
        direction = (params.get("dir") or "asc").lower()
        descending = (direction == "desc")

        result = query_digest(
            app.state.cfg["state"]["db_path"],
            app.state.cfg["state"]["track_db_path"],
            inbox_dir=(app.state.cfg.raw.get("download") or {}).get("inbox_dir"),
            search=filters["q"],
            status_filter=filters["status"],
            download_kind=filters["download_kind"],
            feedback=filters["feedback"],
            sort=sort,
            descending=descending,
            limit=page_size,
            offset=offset,
        )

        def page_url(new_offset: int) -> str:
            query = {key: value for key, value in filters.items() if value}
            query["limit"] = str(page_size)
            if sort:
                query["sort"] = sort
                query["dir"] = direction
            if new_offset > 0:
                query["offset"] = str(new_offset)
            return "/digest?" + urlencode(query)

        def sort_url(field: str) -> str:
            """Link zum Umschalten der Sortierung; Filter und Zeilenmenge bleiben erhalten."""
            query = {key: value for key, value in filters.items() if value}
            query["limit"] = str(page_size)
            query["sort"] = field
            if sort == field:
                query["dir"] = "desc" if direction == "asc" else "asc"
            else:
                query["dir"] = "asc"
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
            current_sort=sort,
            current_dir=direction,
            sort_url=sort_url,
            prev_url=page_url(max(0, offset - page_size)) if offset > 0 else None,
            next_url=page_url(offset + page_size) if offset + page_size < result.total else None,
        ))

    # Kaufempfehlungen aus store_items (Issue #338): dieselbe rein lesende Abfrage für
    # JSON-API und Seite, Bewertungen laufen über das bestehende POST /api/feedback.
    @app.get("/api/kaufliste")
    def api_kaufliste(request: Request):
        params = request.query_params
        result = query_store_items(
            app.state.cfg["state"]["track_db_path"],
            search=params.get("q") or None,
            feedback=params.get("feedback") or None,
            limit=_query_int(params.get("limit"), DEFAULT_LIMIT),
            offset=_query_int(params.get("offset"), 0),
        )
        return JSONResponse(result.to_dict())

    # Verfügbarkeits-Wächter (Issue #494): liefert alle überwachten Stream-Tracks
    # aus availability_watch rein lesend.
    @app.get("/api/watch")
    def api_watch(request: Request):
        items = query_watched_tracks(app.state.cfg["state"]["track_db_path"])
        available_count = sum(1 for x in items if x["is_available"])
        return JSONResponse({
            "total": len(items),
            "available_count": available_count,
            "items": items,
        })

    @app.get("/kaufliste", response_class=HTMLResponse)
    def kaufliste_page(request: Request):
        params = request.query_params
        filters = {
            "q": params.get("q") or "",
            "feedback": params.get("feedback") or "",
        }
        page_size = max(1, min(_query_int(params.get("limit"), DEFAULT_LIMIT), MAX_LIMIT))
        offset = max(0, _query_int(params.get("offset"), 0))
        result = query_store_items(
            app.state.cfg["state"]["track_db_path"],
            search=filters["q"] or None,
            feedback=filters["feedback"] or None,
            limit=page_size,
            offset=offset,
        )
        watched_items = query_watched_tracks(app.state.cfg["state"]["track_db_path"])
        watched_available_count = sum(1 for x in watched_items if x["is_available"])

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
            watched_items=watched_items,
            watched_available_count=watched_available_count,
        ))

    # Quarantäne-Manager (Issue #341): dieselbe rein lesende Abfrage für JSON-API und Seite.
    # Die Freigabe läuft über den Quarantäne-Pfad von State (wie Bot-Befehl /retry <id>),
    # nicht über einen Download: der Track wird nur für den nächsten regulären Lauf frei.
    @app.get("/api/rejected")
    def api_rejected(request: Request):
        params = request.query_params
        return JSONResponse(query_rejected_tracks(
            app.state.cfg["state"]["db_path"],
            search=params.get("q") or None,
            limit=_query_int(params.get("limit"), DEFAULT_LIMIT),
            offset=_query_int(params.get("offset"), 0),
        ))

    @app.get("/rejected", response_class=HTMLResponse)
    def rejected_page(request: Request):
        params = request.query_params
        filters = {"q": params.get("q") or ""}
        page_size = max(1, min(_query_int(params.get("limit"), DEFAULT_LIMIT), MAX_LIMIT))
        offset = max(0, _query_int(params.get("offset"), 0))
        result = query_rejected_tracks(
            app.state.cfg["state"]["db_path"],
            search=filters["q"] or None,
            limit=page_size,
            offset=offset,
        )

        def page_url(new_offset: int) -> str:
            """Link auf eine andere Seite, aktive Filter bleiben erhalten."""
            query = {key: value for key, value in filters.items() if value}
            query["limit"] = str(page_size)
            if new_offset > 0:
                query["offset"] = str(new_offset)
            return "/rejected?" + urlencode(query)

        total_pages = max(1, (result["total"] + page_size - 1) // page_size)
        template = _env.get_template("rejected.html")
        return HTMLResponse(template.render(
            items=result["items"],
            total=result["total"],
            page_size=page_size,
            page=offset // page_size + 1,
            total_pages=total_pages,
            filters=filters,
            prev_url=page_url(max(0, offset - page_size)) if offset > 0 else None,
            next_url=(
                page_url(offset + page_size)
                if offset + page_size < result["total"] else None
            ),
        ))

    @app.post("/api/retry")
    async def api_retry(request: Request):
        if not _same_origin(request):
            return JSONResponse({"ok": False, "error": "Ungültige Herkunft"}, status_code=403)
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Ungültiges JSON"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"ok": False, "error": "Ungültiger Payload"}, status_code=400)
        track_id = payload.get("track_id")
        if track_id is None or isinstance(track_id, bool):
            return JSONResponse(
                {"ok": False, "error": "track_id muss eine Zahl sein"}, status_code=400
            )
        try:
            track_id_int = int(track_id)
        except (TypeError, ValueError):
            return JSONResponse(
                {"ok": False, "error": "track_id muss eine Zahl sein"}, status_code=400
            )
        if retry_quarantined_track(app.state.cfg["state"]["db_path"], track_id_int):
            return JSONResponse({"ok": True})
        return JSONResponse({"ok": False, "error": "Track nicht in Quarantäne"})

    # Interaktive Bewertung aus dem Web (Issue #229): derselbe Speicherpfad wie die
    # Telegram-Buttons (bot.py), damit Digest-Historie, /mix und Statistik dasselbe Feedback
    # sehen. Origin-Prüfung wie beim Konfigeditor, weil die Route schreibt (CSRF).
    @app.post("/api/feedback")
    async def api_feedback(request: Request):
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
        if not save_track_feedback(app.state.cfg["state"]["track_db_path"], sc_id_int, str(value)):
            return JSONResponse(
                {"ok": False, "sc_id": sc_id_int, "value": str(value)}, status_code=500
            )
        return JSONResponse({"ok": True, "sc_id": sc_id_int, "value": str(value)})

    # Matcher-Korrektur aus dem Web-Dashboard (Issue #498): schreibt dieselbe
    # Tabelle match_overrides wie die Telegram-Befehle /is_duplicate und /not_duplicate.
    # Origin-Prüfung wie beim Feedback (CSRF-Schutz).
    @app.post("/api/match-override")
    async def api_match_override(request: Request):
        if not _same_origin(request):
            return JSONResponse({"ok": False, "error": "Ungültige Herkunft"}, status_code=403)
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Ungültiges JSON"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"ok": False, "error": "Ungültiger Payload"}, status_code=400)
        sc_id = payload.get("sc_id")
        override_type = payload.get("override_type")
        if (
            override_type not in ("already_in_collection", "not_duplicate", "clear")
            or isinstance(sc_id, bool)
            or not isinstance(sc_id, int)
        ):
            return JSONResponse(
                {"ok": False, "error": "Ungültige Parameter"},
                status_code=400,
            )
        if not save_match_override(
            app.state.cfg["state"]["track_db_path"], sc_id, str(override_type)
        ):
            return JSONResponse(
                {"ok": False, "sc_id": sc_id, "override_type": override_type},
                status_code=500,
            )
        return JSONResponse({"ok": True, "sc_id": sc_id, "override_type": override_type})

    # Datei-Upload in den Eingangsordner (Issue #230, Gate-Assistent): Stephan legt Dateien
    # ab, die er nach einem Gate (Hypeddit, Droploud, …) selbst am PC geholt hat. Der Upload
    # umgeht das Gate nicht, er ersetzt nur das Kopieren in den Eingangsordner; die Prüfung
    # läuft danach wie beim täglichen Lauf über main.finish_file. Origin-Prüfung wie beim
    # Feedback, weil die Route schreibt (CSRF).
    @app.get("/intake", response_class=HTMLResponse)
    def intake_page(request: Request):
        template = _env.get_template("intake.html")
        return HTMLResponse(template.render(
            results=None,
            summary=None,
            ok=None,
        ))

    @app.post("/api/upload-intake")
    async def api_upload_intake(request: Request):
        if not _same_origin(request):
            return JSONResponse({"ok": False, "error": "Ungültige Herkunft"}, status_code=403)
        # Vor dem Einlesen prüfen: request.form() spoolt den ganzen Body in eine Temp-Datei.
        # Der Header allein ist nicht verlässlich (fehlt bei Chunked), darum zählt
        # process_uploaded_audio beim Kopieren noch einmal mit.
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                declared_bytes = int(declared)
            except ValueError:
                return JSONResponse({"ok": False, "error": "Ungültige Content-Length"}, status_code=400)
            if declared_bytes > MAX_UPLOAD_BYTES + _UPLOAD_OVERHEAD_BYTES:
                return JSONResponse(
                    {"ok": False, "error": "Datei zu groß"}, status_code=413
                )
        try:
            form = await request.form()
        except Exception:
            return JSONResponse({"ok": False, "error": "Kein Multipart-Formular"}, status_code=400)

        # Nimmt mehrere Dateien im wiederholten Feld "files" entgegen; das alte Einzelfeld "file"
        # bleibt gültig.
        uploads: list[Any] = []
        for key, value in form.multi_items():
            if key in ("files", "file") and hasattr(value, "file"):
                uploads.append(value)

        if not uploads:
            return JSONResponse(
                {"ok": False, "error": "Feld 'file' mit einer Audiodatei fehlt"}, status_code=400
            )

        # Prüfung vor der Verarbeitung: Summe der Dateigrößen darf MAX_UPLOAD_BYTES nicht überschreiten
        total_bytes = 0
        for u in uploads:
            sz = getattr(u, "size", None)
            if sz is None and hasattr(u.file, "seek") and hasattr(u.file, "tell"):
                try:
                    pos = u.file.tell()
                    u.file.seek(0, 2)
                    sz = u.file.tell()
                    u.file.seek(pos)
                except Exception:
                    sz = 0
            total_bytes += (sz or 0)

        if total_bytes > MAX_UPLOAD_BYTES:
            return JSONResponse(
                {"ok": False, "error": "Summe der Dateien übersteigt Grenze"}, status_code=413
            )

        # Reihenfolge: in der Reihenfolge der Anfrage, nacheinander im Threadpool
        results = []
        for u in uploads:
            res = await run_in_threadpool(
                process_uploaded_audio, u.file, u.filename or "", app.state.cfg
            )
            results.append(res)

        results_dicts = [asdict(r) for r in results]
        ok_count = sum(1 for r in results if r.ok)
        rej_count = sum(1 for r in results if not r.ok and r.status == "rejected")
        err_count = sum(1 for r in results if not r.ok and r.status != "rejected")

        all_ok = (ok_count == len(results)) and len(results) > 0
        summary = {"ok": ok_count, "rejected": rej_count, "error": err_count}

        if all_ok:
            status_code = 200
        elif ok_count > 0:
            status_code = 207
        elif any(r.status == "too_large" for r in results) and (len(results) == 1 or all(r.status == "too_large" for r in results)):
            status_code = 413
        else:
            status_code = 422

        # HTML-Fallback für Formularabsendung ohne JavaScript
        if request.headers.get("sec-fetch-dest") == "document":
            template = _env.get_template("intake.html")
            return HTMLResponse(
                template.render(
                    results=results_dicts,
                    summary=summary,
                    ok=all_ok,
                ),
                status_code=status_code,
            )

        response_data: dict[str, Any] = {
            "ok": all_ok,
            "results": results_dicts,
            "summary": summary,
        }
        # Abwärtskompatibilität für alte Tests und Einzelaufrufe
        if results_dicts:
            first = results_dicts[0]
            response_data["filename"] = first.get("filename")
            response_data["status"] = first.get("status")
            response_data["message"] = first.get("message")
            response_data["dest_path"] = first.get("dest_path")
            response_data["bpm"] = first.get("bpm")
            response_data["key_camelot"] = first.get("key_camelot")

        return JSONResponse(response_data, status_code=status_code)

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

    def _render_config(section: ConfigSection, base: dict, local: dict, problems: list,
                       form: dict[str, str] | None = None, saved: bool = False,
                       status_code: int = 200) -> HTMLResponse:
        values = _form_values(base, local, form)
        defaults = {f.path: format_form_value(f, get_path(base, f.path))
                    for f in section.fields}
        local_paths = {f.path for f in section.fields
                       if get_path(local, f.path, _MISSING) is not _MISSING}
        template = _env.get_template("config.html")
        return HTMLResponse(template.render(
            sections=config_sections(), section=section, values=values, defaults=defaults,
            local_paths=local_paths, problems=problems, saved=saved,
            section_description=SECTION_DESCRIPTIONS.get(section.title),
            save_action="/config/save", preview_action="/config/preview",
            reset_action="/config/reset", section_field=SECTION_FIELD,
        ), status_code=status_code)

    def _section_of_form(form: dict[str, str]) -> ConfigSection | None:
        """Abschnitt aus dem Formularfeld `_section` (nimmt es aus dem Formular heraus)."""
        slug = form.pop(SECTION_FIELD, "")
        return config_section(slug) if slug else None

    @app.get("/config")
    def config_index(request: Request):
        # Das Gespeichert-Banner übersteht die Umleitung über den Query-String.
        query = "?" + request.url.query if request.url.query else ""
        return RedirectResponse(f"/config/{config_sections()[0].slug}{query}", status_code=302)

    @app.get("/config/{section_id}", response_class=HTMLResponse)
    def config_page(section_id: str, request: Request):
        section = config_section(section_id)
        if section is None:
            return PlainTextResponse("Unbekannter Abschnitt", status_code=404)
        base, local = _read_files()
        saved = request.query_params.get("saved") == "1"
        return _render_config(section, base, local, problems=[], saved=saved)

    @app.post("/config/preview")
    async def config_preview(request: Request):
        if not _same_origin(request):
            return PlainTextResponse("Ungültige Herkunft", status_code=403)
        form = {key: value for key, value in (await request.form()).items()}
        section = _section_of_form(form)
        if section is not None:
            form = _default_bools_false(section, _only_section_fields(form, section))
        base, local = _read_files()
        new_local, problems = apply_form(base, local, form)
        if any(problem.level == "error" for problem in problems):
            return _render_config(section or config_sections()[0], base, local,
                                  problems=problems, form=form, status_code=422)
        changes = diff_config(deep_merge(base, local), deep_merge(base, new_local))
        warnings = [problem for problem in problems if problem.level == "warning"]
        template = _env.get_template("config_preview.html")
        back = f"/config/{section.slug}" if section else "/config"
        return HTMLResponse(template.render(changes=changes, warnings=warnings, config_action=back))

    @app.post("/config/save")
    async def config_save(request: Request):
        if not _same_origin(request):
            return PlainTextResponse("Ungültige Herkunft", status_code=403)
        form = {key: value for key, value in (await request.form()).items()}
        section = _section_of_form(form)
        if section is not None:
            form = _default_bools_false(section, _only_section_fields(form, section))
        base, local = _read_files()
        new_local, problems = apply_form(base, local, form)
        if any(problem.level == "error" for problem in problems):
            return _render_config(section or config_sections()[0], base, local,
                                  problems=problems, form=form, status_code=422)
        if new_local != local:
            save_local(_local_path, new_local)
            app.state.cfg = Config.load(_base_path, local=_local_path)
        target = f"/config/{section.slug}" if section else "/config"
        return RedirectResponse(f"{target}?saved=1", status_code=303)

    @app.post("/config/reset")
    async def config_reset(request: Request):
        if not _same_origin(request):
            return PlainTextResponse("Ungültige Herkunft", status_code=403)
        path = str((await request.form()).get("path", ""))
        field = get_field(path)
        base, local = _read_files()
        if field is None or not field.editable:
            problem = Problem("error", path, "Dieser Schlüssel ist nicht editierbar.")
            return _render_config(config_sections()[0], base, local, problems=[problem],
                                  status_code=422)
        save_local(_local_path, remove_key(local, path))
        app.state.cfg = Config.load(_base_path, local=_local_path)
        # Der Abschnitt folgt aus dem Feld selbst, nicht aus dem Formular.
        section = config_section_of_field(path)
        target = f"/config/{section.slug}" if section else "/config"
        return RedirectResponse(f"{target}?saved=1", status_code=303)

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


SECTION_FIELD = "_section"


@dataclass(frozen=True)
class ConfigSection:
    """Ein Abschnitt des Konfigeditors: stabiler URL-Name, Überschrift, editierbare Felder."""
    slug: str
    title: str
    fields: tuple


_TRANSLIT = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}


def slugify_section(title: str) -> str:
    """URL-Name eines Abschnitts: klein, nur a-z0-9 und Bindestrich, Umlaute umgeschrieben."""
    text = "".join(_TRANSLIT.get(ch, ch) for ch in title.lower())
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


_SECTIONS_CACHE: list[ConfigSection] = []


def config_sections() -> list[ConfigSection]:
    """Alle Abschnitte in Schema-Reihenfolge; gleiche Überschriften werden zusammengefasst.

    Das Schema ist zur Laufzeit konstant, deshalb einmal berechnet. Kollidieren zwei
    Überschriften auf denselben URL-Namen, ist das ein Programmierfehler und bricht laut ab,
    statt dass ein Abschnitt unerreichbar würde.
    """
    if not _SECTIONS_CACHE:
        grouped: dict[str, list] = {}
        for field in FIELDS:
            if field.editable:
                grouped.setdefault(_section_title(field.path), []).append(field)
        sections = [ConfigSection(slugify_section(title), title, tuple(fields))
                    for title, fields in grouped.items()]
        slugs = [section.slug for section in sections]
        if len(set(slugs)) != len(slugs) or not all(slugs):
            raise ValueError(f"URL-Namen der Konfig-Abschnitte nicht eindeutig: {slugs}")
        _SECTIONS_CACHE.extend(sections)
    return _SECTIONS_CACHE


def config_section(slug: str) -> ConfigSection | None:
    return next((s for s in config_sections() if s.slug == slug), None)


def config_section_of_field(path: str) -> ConfigSection | None:
    return next((s for s in config_sections() if any(f.path == path for f in s.fields)), None)


def _only_section_fields(form: dict[str, str], section: ConfigSection) -> dict[str, str]:
    """Verwirft Formularwerte fremder Abschnitte, damit ein Abschnitts-Save nur den eigenen ändert."""
    allowed = {f.path for f in section.fields}
    return {key: value for key, value in form.items() if key in allowed}

def _default_bools_false(section: ConfigSection, form: dict[str, str]) -> dict[str, str]:
    """Ergänzt fehlende Boolean-Felder des Abschnitts mit „false“.

    Eine nicht angehakte Checkbox wird vom Browser gar nicht gesendet; ein verstecktes
    Feld gleichen Namens käme wegen der Dict-Umwandlung des Formulars nicht an. Nur Felder
    des abgeschickten Abschnitts, damit andere Abschnitte nicht auf „false“ fallen."""
    ergaenzt = dict(form)
    for field in section.fields:
        if field.kind == "bool" and field.path not in ergaenzt:
            ergaenzt[field.path] = "false"
    return ergaenzt
