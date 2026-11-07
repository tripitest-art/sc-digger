"""Gemeinsame Helfer für die FastAPI-Web-App und ihre Bereichs-Router."""
from __future__ import annotations

import math
import mimetypes
import re
import secrets
import sqlite3
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from fastapi import Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..models import Config
from ..soundcloud import SoundCloudError
from .audio import parse_range_header, resolve_playable_path
from .status import change_badge

_TEMPLATES = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES)),
    autoescape=select_autoescape(("html", "xml")),
)
# Vergleich mit der Vorperiode (Issue #488) steht der Statusseite als Template-Funktion bereit.
_env.globals["change_badge"] = change_badge


def _render(template_name: str, *, status_code: int = 200, **context: Any) -> HTMLResponse:
    """Rendert ein Jinja2-Template aus dem Web-Templates-Ordner als HTMLResponse."""
    template = _env.get_template(template_name)
    return HTMLResponse(template.render(**context), status_code=status_code)


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
    # compare_digest wirft TypeError, sobald ein str Nicht-ASCII enthält (z. B. ein
    # Passwort mit Umlaut). Deshalb als bytes vergleichen: gleiche Prüfung, kein 500.
    return secrets.compare_digest(given.encode("utf-8"), (password or "").encode("utf-8"))


# Einzige Pfade ohne Anmeldung. Alles andere (auch unbekannte Pfade) verlangt Basic-Auth:
# Die Prüfung sitzt in einer Middleware (Default-deny), nicht in den einzelnen Handlern.
PUBLIC_PATHS = frozenset({"/healthz"})

# Login-Sperre: Basic-Auth kennt nur ein Passwort, ohne Bremse ließe es sich beliebig oft
# durchprobieren. Fünf Fehlversuche in fünf Minuten sperren die Client-IP für 15 Minuten.
# Multipart-Rahmen (Grenzen, Feldnamen) zählt zur Content-Length, aber nicht zur Dateigröße.
_UPLOAD_OVERHEAD_BYTES = 1024 * 1024
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 300.0
LOGIN_LOCKOUT_SECONDS = 900.0


class FailureTracker:
    """Zählt Fehlversuche je Schlüssel (Client-IP) im Gleitfenster und sperrt zeitweise.

    Nur im Arbeitsspeicher: Ein Neustart hebt Sperren auf. Das ist bewusst so, die Sperre
    bremst Durchprobieren, sie ist keine Buchführung.
    """

    def __init__(
        self,
        *,
        max_failures: int = LOGIN_MAX_FAILURES,
        window_seconds: float = LOGIN_WINDOW_SECONDS,
        lockout_seconds: float = LOGIN_LOCKOUT_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._max = max_failures
        self._window = window_seconds
        self._lockout = lockout_seconds
        self._clock = clock or time.monotonic
        self._failures: dict[str, deque[float]] = {}
        self._locked_until: dict[str, float] = {}

    def is_locked(self, key: str) -> float:
        """Restsekunden der Sperre; 0.0 heißt frei. Abgelaufene Sperren räumt der Aufruf ab."""
        until = self._locked_until.get(key)
        if until is None:
            return 0.0
        remaining = until - self._clock()
        if remaining <= 0:
            del self._locked_until[key]
            return 0.0
        return remaining

    def fail(self, key: str) -> None:
        now = self._clock()
        stamps = self._failures.setdefault(key, deque())
        stamps.append(now)
        # Fehlversuche außerhalb des Fensters zählen nicht mehr.
        while stamps and now - stamps[0] > self._window:
            stamps.popleft()
        if len(stamps) >= self._max:
            self._locked_until[key] = now + self._lockout
            del self._failures[key]

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)


def client_key(request: Request, *, trust_proxy: bool = False) -> str:
    """Schlüssel für die Login-Sperre: der Socket-Peer, hinter einem Proxy die letzte Station.

    Die erste Adresse in X-Forwarded-For kann der Client frei erfinden; erst die letzte hat
    der eigene Proxy angehängt. Ohne trust_proxy bleibt der Header ganz unbeachtet.
    """
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unbekannt"


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


def _require_json_content_type(
    request: Request, *, allow_empty_body: bool = False
) -> JSONResponse | None:
    """Verlangt 'Content-Type: application/json' zur Abwehr von Cross-Origin Simple Requests (Issue #415).

    Browser verweigern 'application/json' als Simple Request und erzwingen einen Preflight (OPTIONS),
    sodass fremde Webseiten keine unautorisierten Zustandsänderungen auslösen können.
    Anfragen mit abweichendem Content-Type (z. B. text/plain) oder fehlendem Content-Type werden mit
    HTTP 415 (Unsupported Media Type) abgelehnt.
    """
    if ("origin" in request.headers or "referer" in request.headers) and not _same_origin(request):
        return JSONResponse(
            {"ok": False, "error": "Cross-Origin-Anfragen sind nicht erlaubt"},
            status_code=403,
        )

    ct = request.headers.get("content-type", "").strip()
    media_type = ct.split(";")[0].strip().lower() if ct else ""

    if media_type == "application/json":
        return None

    if not ct and allow_empty_body and request.headers.get("content-length", "0") in ("0", ""):
        return None

    return JSONResponse(
        {"ok": False, "error": "Content-Type muss application/json sein"},
        status_code=415,
    )


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


def _xml_filename(name: str) -> str:
    """Unbedenklicher Dateiname für ``Content-Disposition`` (keine Quotes/Umbrüche)."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", _m3u8_safe(name)).strip("._-")
    return f"{stem or 'set'}.xml"


class _ReadOnlyTrackDB:
    """Hülle, die dem Mix-Finder nur ``db.db`` (die ro-Verbindung) durchreicht.

    ``TrackDB.__init__`` legt fehlende Dateien und das Schema an (Schreibzugriff), deshalb
    darf die lesende GET-Route keine echte ``TrackDB`` benutzen. ``find_mix_candidates`` braucht
    lediglich ein Objekt mit einem ``db``-Attribut (``sqlite3.Connection``).
    """

    def __init__(self, connection: sqlite3.Connection):
        self.db = connection


# Feste Meldung für den Browser. Die Route gibt die Konstante zurück, nicht str(exc):
# so fließt kein Ausnahmetext nach außen (CodeQL py/stack-trace-exposure).
_SCAN_NOT_TRACK_OR_PLAYLIST = "Das ist weder ein Track- noch ein Playlist-Link."


class _ScanLinkError(SoundCloudError):
    """Eingabefehler im URL-Scanner; der Browser sieht _SCAN_NOT_TRACK_OR_PLAYLIST."""


_AUDIO_BLOCK_SIZE = 64 * 1024


def _playable_roots(cfg: Config) -> list[Path]:
    """Erlaubte Wurzeln fürs Vorhören aus ``download.inbox_dir`` und ``collection_dir``."""
    download = cfg.raw.get("download") or {}
    return [
        Path(str(download[key]))
        for key in ("inbox_dir", "collection_dir")
        if download.get(key)
    ]


def _iter_file_range(path: Path, start: int, length: int):
    """Datei blockweise lesen, damit große Tracks nicht komplett im Speicher landen."""
    with open(path, "rb") as handle:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(_AUDIO_BLOCK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _audio_response(request: Request, path_value: str | None, cfg: Config) -> Response:
    """Liefert eine Audiodatei aus Inbox oder Sammlung, mit Range-Unterstützung.

    404, wenn der Pfad fehlt, per Symlink aus den erlaubten Wurzeln herausführt oder nicht
    lesbar ist. Nur lesend; die Sammlung bleibt unberührt.
    """
    path = resolve_playable_path(path_value or "", _playable_roots(cfg))
    if path is None:
        return PlainTextResponse("Datei nicht verfügbar", status_code=404)
    try:
        size = path.stat().st_size
    except OSError:
        return PlainTextResponse("Datei nicht verfügbar", status_code=404)
    try:
        byte_range = parse_range_header(request.headers.get("range"), size)
    except ValueError:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
        )
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if byte_range is None:
        start, end = 0, size - 1
        status_code = 200
    else:
        start, end = byte_range
        status_code = 206
    length = end - start + 1
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(length)}
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        _iter_file_range(path, start, length),
        status_code=status_code,
        media_type=media_type,
        headers=headers,
    )
