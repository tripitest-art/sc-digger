"""Navigation des Web-Dashboards nach Arbeitsbereichen.

Die Zuordnung Seite -> Bereich steht nur hier. Vorher setzte jede Seite einen eigenen
``{% block nav_… %}``; dabei konnte eine neue Seite die Markierung vergessen, und die
Reihenfolge der Reiter war über base.html und alle Templates verteilt.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class NavEntry:
    label: str
    href: str


@dataclass(frozen=True)
class NavGroup:
    key: str
    label: str
    entries: tuple[NavEntry, ...]

    @property
    def href(self) -> str:
        """Ziel des Bereichs-Reiters: die erste Seite des Bereichs."""
        return self.entries[0].href


# Reihenfolge = Weg eines Tracks: entdecken, hereinholen, verwalten, auflegen.
NAV_GROUPS: tuple[NavGroup, ...] = (
    NavGroup("start", "Start", (NavEntry("Übersicht", "/"),)),
    NavGroup("entdecken", "Entdecken", (
        NavEntry("Digest", "/digest"),
        NavEntry("Kuratoren", "/kuratoren"),
        NavEntry("Kaufliste", "/kaufliste"),
    )),
    NavGroup("eingang", "Eingang", (
        NavEntry("Upload", "/intake"),
        NavEntry("Quarantäne", "/rejected"),
    )),
    NavGroup("bibliothek", "Bibliothek", (
        NavEntry("Tracks", "/tracks"),
        NavEntry("Duplikate", "/duplicates"),
    )),
    NavGroup("auflegen", "Auflegen", (NavEntry("Mix", "/mix"),)),
)


@dataclass(frozen=True)
class NavState:
    groups: tuple[NavGroup, ...]
    active_group: NavGroup | None
    active_entry: NavEntry | None
    sub_entries: tuple[NavEntry, ...]


def _matches(path: str, href: str) -> bool:
    if path == href:
        return True
    # "/" wäre sonst Präfix jedes Pfads und machte "Start" überall aktiv.
    return href != "/" and path.startswith(href + "/")


def find_active(path: str) -> tuple[NavGroup | None, NavEntry | None]:
    """Bereich und Seite zu einem URL-Pfad; ohne Treffer (None, None)."""
    if not isinstance(path, str) or not path:
        return None, None
    for group in NAV_GROUPS:
        for entry in group.entries:
            if _matches(path, entry.href):
                return group, entry
    return None, None


# Pfad der aktuellen Anfrage. Wie beim Zahnrad in app.py: Die Jinja-Umgebung ist modulweit,
# der Pfad gehört zur Anfrage, also setzt ihn die Middleware und base.html fragt ihn ab.
_request_path: ContextVar[str] = ContextVar("sc_digger_nav_path", default="")


def nav_state() -> NavState:
    """Navigationszustand der aktuellen Anfrage (Template-Funktion in base.html)."""
    group, entry = find_active(_request_path.get())
    sub = group.entries if group is not None and len(group.entries) > 1 else ()
    return NavState(groups=NAV_GROUPS, active_group=group, active_entry=entry, sub_entries=sub)


def register(app) -> None:
    """Middleware für den Anfragepfad anhängen und `nav_state` im Template bereitstellen."""
    from .common import _env

    _env.globals["nav_state"] = nav_state

    @app.middleware("http")
    async def remember_nav_path(request, call_next):
        _request_path.set(request.url.path)
        return await call_next(request)
