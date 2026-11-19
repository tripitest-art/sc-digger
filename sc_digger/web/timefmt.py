"""Ortszeit im Web-Dashboard (Issue #621).

Die Datenbanken speichern UTC. Der Jinja-Filter `localtime` rechnet in die Zone aus
`web.timezone` um. Die Zone liegt in einer ContextVar, die eine Middleware je Anfrage aus der
aktuellen Konfiguration setzt – so greift eine Änderung im Konfigeditor ohne Neustart."""
from __future__ import annotations

import contextvars
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Europe/Berlin"

_request_zone: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "timefmt_zone", default=None)


def _parse_utc(text: str) -> datetime | None:
    """Liest die in den DBs üblichen Schreibweisen; naive Werte gelten als UTC."""
    s = text.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1]
    try:
        dt = datetime.fromisoformat(s.replace(" ", "T", 1))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_local(value: str | None, tz_name: str | None = None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """UTC-Text in die Zone tz_name (sonst Zone der Anfrage, sonst Standard) umrechnen.
    None/"" → "—", unlesbarer Text bleibt unverändert, unbekannte Zone → UTC-Zeit + " UTC".
    Wirft nie, damit eine kaputte Zeile nie eine ganze Seite verhindert."""
    try:
        if value is None or str(value).strip() == "":
            return "—"
        text = str(value)
        dt = _parse_utc(text)
        if dt is None:
            return text
        zone_name = tz_name or _request_zone.get() or DEFAULT_TIMEZONE
        try:
            zone = ZoneInfo(zone_name)
        except Exception:
            return dt.strftime(fmt) + " UTC"
        return dt.astimezone(zone).strftime(fmt)
    except Exception:
        return str(value)


def register(app) -> None:
    """Filter `localtime` eintragen und die Zone je Anfrage aus der Konfiguration setzen."""
    from .common import _env

    _env.filters["localtime"] = to_local

    @app.middleware("http")
    async def remember_timezone(request, call_next):
        zone = None
        try:
            web = app.state.cfg.raw.get("web") or {}
            zone = web.get("timezone") or None
        except Exception:
            zone = None
        _request_zone.set(zone or DEFAULT_TIMEZONE)
        return await call_next(request)
