"""MixesDB: Set-Tracklisten aus dem Szene-Wiki lesen und mit der Sammlung abgleichen.

MixesDB (mixesdb.com) dokumentiert DJ-Sets als Wikiseiten; Schranz und Hard Techno sind
dort vollständig erfasst, während 1001tracklists die Szene praktisch nicht kennt. Die
Tracklisten nennen auch Promos, White Labels und Edits, die keine Audio-Erkennung findet.

Dieses Modul liest die Tracklist einer Seite über die MediaWiki-API (action=parse,
wikitext) — das ist stabiler als HTML — und vergleicht die Einträge über
:mod:`sc_digger.collection` mit der eigenen Sammlung. Netzwerkzugriff nur in
:func:`search_sets` und :func:`fetch_setlist`; beide nehmen einen Fetcher entgegen, damit
Tests ohne Netz laufen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import re
from typing import Callable, Iterable, Sequence
from urllib.parse import urlencode

from .collection import NameIndex, name_key

log = logging.getLogger(__name__)

MIXESDB_API = "https://www.mixesdb.com/w/api.php"
MIXESDB_WIKI = "https://www.mixesdb.com/w/"
USER_AGENT = "sc-digger/1.0 (+https://github.com/tripitest-art/sc-digger)"
TIMEOUT_S = 20

# Ein Fetcher bekommt eine URL und liefert den Rumpf der Antwort oder ``None`` bei jedem
# Fehler (Netz, HTTP-Status, kaputtes JSON). Er wirft nie.
Fetch = Callable[[str], "str | None"]

# ``[48]`` ist in MixesDB die Minutenposition im Set, ``[48:30]`` Minuten und Sekunden.
_TIMESTAMP_RE = re.compile(r"^\[(\d{1,3})(?::(\d{2}))?\]\s*")
# Listenmarker: ``#``, ``*``, ``#*`` und die Zeilen, die MixesDB innerhalb von <list> setzt.
_MARKER_RE = re.compile(r"^(?:<br\s*/?>|#+|\*+)\s*", re.IGNORECASE)
# Wikimarkup, das in Tracklisten vorkommt: Links, Fettschrift, Kommentare.
_LINK_RE = re.compile(r"\[\[([^\]|]*)\|([^\]]*)\]\]|\[\[([^\]]*)\]\]")
_QUOTES_RE = re.compile(r"'{2,}")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# Abschließende Klammer mit Label/Katalognummer: ``[Schlachthof - 2006001]``, ``[Kube]``.
_LABEL_RE = re.compile(r"\s*\[([^\[\]]{1,80})\]$")
# Einträge, die als "nicht benannt" gelten.
_OPEN_RE = re.compile(
    r"^(?:\?+|id|id\s*-\s*id|unbekannt|unknown|tba|id\?)$", re.IGNORECASE
)
# Fortsetzungspunkte mitten in einer Tracklist ("..." heißt: hier fehlen Einträge).
_ELLIPSIS_RE = re.compile(r"^(?:\.{2,}|…+)$")
# ``| 2:05:00`` in der Datei-Tabelle ist die Setdauer.
_DURATION_RE = re.compile(r"^\|\s*(\d{1,2}):(\d{2}):(\d{2})\s*$", re.MULTILINE)
_HEADING_RE = re.compile(r"^==\s*([^=].*?)\s*==\s*$", re.MULTILINE)


@dataclass(frozen=True)
class SetlistEntry:
    """Ein Eintrag einer MixesDB-Tracklist.

    ``position_s`` ist die im Wiki angegebene Position in Sekunden (``None``, wenn der
    Eintrag keine Zeitangabe hat). Ein offener Eintrag (``?``) hat leeren Artist und
    Titel; er markiert eine Stelle, die auch das Wiki nicht benennt.
    """

    position_s: int | None
    artist: str
    title: str
    label: str | None = None
    catalog: str | None = None

    @property
    def unknown(self) -> bool:
        """True für ``?``-Einträge: im Wiki nicht benannt."""
        return not self.artist and not self.title

    @property
    def position_label(self) -> str:
        """Position als ``m:ss`` für Ausgaben; ``""`` ohne Zeitangabe."""
        if self.position_s is None:
            return ""
        return f"{self.position_s // 60}:{self.position_s % 60:02d}"

    def track(self) -> str:
        """``Artist - Titel`` bzw. nur der Titel, wenn kein Artist im Wiki steht."""
        if self.unknown:
            return "?"
        return f"{self.artist} - {self.title}" if self.artist else self.title


@dataclass(frozen=True)
class Setlist:
    """Die Tracklist einer MixesDB-Seite."""

    page: str
    entries: tuple[SetlistEntry, ...] = ()
    duration_s: int | None = None

    @property
    def url(self) -> str:
        return setlist_url(self.page)

    @property
    def open_entries(self) -> tuple[SetlistEntry, ...]:
        """Die ``?``-Stellen — dort hilft auch das Wiki nicht weiter."""
        return tuple(e for e in self.entries if e.unknown)

    def named_entries(self) -> tuple[SetlistEntry, ...]:
        """Alle benannten Einträge, in Wiki-Reihenfolge."""
        return tuple(e for e in self.entries if not e.unknown)


@dataclass(frozen=True)
class SetlistCheck:
    """Ergebnis des Abgleichs einer Tracklist mit der eigenen Sammlung.

    ``owned`` steht parallel zu ``setlist.entries``: ``True`` = Track ist in der Sammlung,
    ``False`` = fehlt, ``None`` = offener Eintrag ohne Namen.
    """

    setlist: Setlist
    owned: tuple[bool | None, ...] = field(default=())

    @property
    def missing(self) -> tuple[SetlistEntry, ...]:
        """Benannte Einträge, die in der Sammlung fehlen."""
        return tuple(
            entry
            for entry, has in zip(self.setlist.entries, self.owned)
            if has is False
        )

    @property
    def present(self) -> tuple[SetlistEntry, ...]:
        """Einträge, die in der Sammlung liegen."""
        return tuple(
            entry for entry, has in zip(self.setlist.entries, self.owned) if has is True
        )


# --------------------------------------------------------------------- Parsen


def _clean(line: str) -> str:
    """Entfernt Wikimarkup, das den Namen nicht verändert (Links, Quotes, Kommentare)."""
    text = _COMMENT_RE.sub("", line)
    text = _LINK_RE.sub(lambda m: m.group(2) or m.group(3) or m.group(1) or "", text)
    text = _QUOTES_RE.sub("", text)
    return " ".join(text.split())


def _split_label(payload: str) -> tuple[str, str | None, str | None]:
    """Trennt eine abschließende ``[Label - Katalog]``-Klammer ab."""
    match = _LABEL_RE.search(payload)
    if not match:
        return payload, None, None
    rest = payload[: match.start()].strip()
    label, sep, catalog = match.group(1).partition(" - ")
    label = label.strip()
    catalog = catalog.strip() if sep else None
    return rest, (label or None), (catalog or None)


def parse_position(text: str) -> tuple[int | None, str]:
    """Liest eine führende ``[N]``/``[N:NN]``-Zeitangabe.

    MixesDB schreibt Minuten (``[048]`` für 48 Minuten); ``[48:30]`` wird als Minuten und
    Sekunden gelesen. Rückgabe: Position in Sekunden und der Rest der Zeile.
    """
    match = _TIMESTAMP_RE.match(text)
    if not match:
        return None, text
    minutes = int(match.group(1))
    seconds = int(match.group(2) or 0)
    return minutes * 60 + seconds, text[match.end():]


def parse_entry_line(line: str) -> SetlistEntry | None:
    """Parst eine Zeile einer Tracklist; ``None`` für Zeilen ohne Eintrag.

    Erkannt werden ``[048] Artist - Titel [Label - Katalog]``, ``# Artist - Titel``,
    ``# [02] Artist - Titel [Label]``, ``Artist - Titel`` ohne Artistangabe und ``?``.
    """
    text = _clean(line).strip()
    if not text:
        return None
    text = _MARKER_RE.sub("", text).strip()
    if not text:
        return None
    # Kopfzeilen, Tabellen und Vorlagen sind keine Einträge.
    if text.startswith(("{{", "{|", "|}", "|", "!", "==", "[[Category:", "Category:")):
        return None
    if text.startswith("<list") or text.startswith("</list"):
        return None

    position_s, rest = parse_position(text)
    rest = rest.strip()
    if not rest:
        return None
    if _ELLIPSIS_RE.match(rest):
        return None
    if _OPEN_RE.match(rest):
        return SetlistEntry(position_s=position_s, artist="", title="")

    rest, label, catalog = _split_label(rest)
    # Erneut prüfen: ``Artist - Titel [?]`` bleibt ein benannter Eintrag.
    if not rest:
        return None
    if _OPEN_RE.match(rest):
        return SetlistEntry(position_s=position_s, artist="", title="")

    artist, sep, title = rest.partition(" - ")
    artist = artist.strip()
    title = title.strip()
    if not sep:
        # Ohne " - " steht in der Zeile nur ein Titel; der Artist fehlt im Wiki.
        return SetlistEntry(
            position_s=position_s, artist="", title=rest, label=label, catalog=catalog
        )
    if not title:
        return None
    return SetlistEntry(
        position_s=position_s, artist=artist, title=title, label=label, catalog=catalog
    )


def _tracklist_section(wikitext: str) -> str:
    """Schneidet den ``== Tracklist ==``-Abschnitt heraus (ohne Kategorien)."""
    text = _COMMENT_RE.sub("", wikitext)
    start = None
    for match in _HEADING_RE.finditer(text):
        if match.group(1).strip().lower() == "tracklist":
            start = match.end()
            break
    if start is None:
        return ""
    rest = text[start:]
    # Der Abschnitt endet an der nächsten Überschrift gleicher oder höherer Ebene.
    end = len(rest)
    for match in _HEADING_RE.finditer(rest):
        end = match.start()
        break
    section = rest[:end]
    category = section.find("[[Category:")
    return section[:category] if category >= 0 else section


def parse_setlist(wikitext: str, *, page: str = "") -> Setlist:
    """Parst eine MixesDB-Seite zu einer :class:`Setlist`; wirft nie.

    Leere Tracklisten (``Tracklist: none``) ergeben eine Setlist ohne Einträge.
    """
    section = _tracklist_section(wikitext)
    entries: list[SetlistEntry] = []
    for raw in section.splitlines():
        entry = parse_entry_line(raw)
        if entry is not None:
            entries.append(entry)

    duration_s = None
    match = _DURATION_RE.search(wikitext)
    if match:
        hours, minutes, seconds = (int(g) for g in match.groups())
        duration_s = hours * 3600 + minutes * 60 + seconds
    return Setlist(page=page, entries=tuple(entries), duration_s=duration_s)


# --------------------------------------------------------------------- Netzwerk


def setlist_url(page: str) -> str:
    """Die lesbare Wiki-Adresse einer Seite (Leerzeichen als Unterstriche)."""
    return MIXESDB_WIKI + page.replace(" ", "_")


def _http_get(url: str) -> str | None:
    """Holt eine URL; ``None`` bei jedem Fehler (kein Werfen, Netz ist optional)."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - Netzfehler dürfen den Lauf nicht stoppen
        log.warning("MixesDB nicht erreichbar (%s): %s", url, exc)
        return None


def _fetch_wikitext(page: str, fetch: Fetch) -> str | None:
    """Holt den Wikitext einer Seite über die API; ``None``, wenn die Seite fehlt."""
    query = urlencode(
        {
            "action": "parse",
            "page": page,
            "prop": "wikitext",
            "format": "json",
            "formatversion": "1",
            "redirects": "1",
        }
    )
    body = fetch(f"{MIXESDB_API}?{query}")
    if not body:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        log.warning("MixesDB-Antwort für %r ist kein JSON", page)
        return None
    parse = data.get("parse") if isinstance(data, dict) else None
    if not isinstance(parse, dict):
        log.info("MixesDB-Seite %r existiert nicht", page)
        return None
    wikitext = parse.get("wikitext")
    if isinstance(wikitext, dict):
        wikitext = wikitext.get("*")
    return wikitext if isinstance(wikitext, str) else None


def fetch_setlist(page: str, *, fetch: Fetch | None = None) -> Setlist | None:
    """Lädt und parst die Tracklist einer Seite; ``None``, wenn die Seite nicht existiert."""
    wikitext = _fetch_wikitext(page, fetch or _http_get)
    if wikitext is None:
        return None
    return parse_setlist(wikitext, page=page)


def search_sets(
    query: str, *, limit: int = 10, fetch: Fetch | None = None
) -> list[str]:
    """Sucht Set-Seiten zu einer Anfrage (DJ, Event, Jahr) und liefert die Seitentitel.

    Leere Anfrage oder jeder Fehler ergibt eine leere Liste.
    """
    if not query.strip():
        return []
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": str(max(1, min(int(limit), 50))),
        "srnamespace": "0",
        "format": "json",
    }
    body = (fetch or _http_get)(f"{MIXESDB_API}?{urlencode(params)}")
    if not body:
        return []
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        log.warning("MixesDB-Suche lieferte kein JSON")
        return []
    hits = ((data or {}).get("query") or {}).get("search") or []
    return [hit["title"] for hit in hits if isinstance(hit, dict) and "title" in hit]


# --------------------------------------------------------------------- Abgleich


def build_name_index(
    tracks: Iterable[tuple[str | None, str]],
) -> NameIndex:
    """Baut einen :class:`NameIndex` aus ``(artist, title)``-Paaren.

    Label eines Eintrags ist ``"Artist - Titel"``; damit findet
    :meth:`check_setlist` die Wiki-Einträge wieder, die schon in der Sammlung liegen.
    """
    index = NameIndex()
    for artist, title in tracks:
        if not (title or "").strip():
            continue
        label = f"{artist} - {title}" if artist else title
        index.add(label, name_key(artist, title))
    return index


def check_setlist(
    setlist: Setlist,
    tracks: Iterable[tuple[str | None, str]] | NameIndex,
) -> SetlistCheck:
    """Vergleicht die Tracklist mit der Sammlung (``(artist, title)``-Paare oder Index)."""
    index = tracks if isinstance(tracks, NameIndex) else build_name_index(tracks)
    owned: list[bool | None] = []
    for entry in setlist.entries:
        if entry.unknown:
            owned.append(None)
            continue
        key = name_key(entry.artist or None, entry.title)
        owned.append(bool(index.find_all(key)))
    return SetlistCheck(setlist=setlist, owned=tuple(owned))


def format_setlist(check: SetlistCheck, *, show_owned: bool = True) -> str:
    """Textfassung eines Abgleichs für Terminal und Telegram.

    Zeilen: Position, ``✓``/``✗``/``?``, ``Artist - Titel [Label - Katalog]``. Offene
    Stellen (``?``) erscheinen immer, vorhandene Tracks nur mit ``show_owned``.
    """
    entries = check.setlist.entries
    named = [e for e in entries if not e.unknown]
    head = (
        f"{check.setlist.page}: {len(entries)} Einträge, "
        f"{len(check.missing)} fehlen, {len(check.present)} vorhanden, "
        f"{len(check.setlist.open_entries)} offen"
    )
    lines = [head]
    for entry, has in zip(entries, check.owned):
        if entry.unknown:
            mark = "?"
        else:
            mark = "✓" if has else "✗"
            if has and not show_owned:
                continue
        line = f"{mark} {entry.position_label:>6}  {entry.track()}".rstrip()
        if entry.label:
            line += f"  [{entry.label}"
            if entry.catalog:
                line += f" - {entry.catalog}"
            line += "]"
        lines.append(line)
    if not named:
        lines.append("  (keine benannten Einträge — Wiki-Tracklist unvollständig)")
    return "\n".join(lines)


def missing_tracks(check: SetlistCheck) -> list[str]:
    """Die fehlenden Tracks als ``"Artist - Titel [Label - Katalog]"``-Zeilen."""
    return [
        _entry_line(entry) for entry in check.missing
    ]


def _entry_line(entry: SetlistEntry) -> str:
    """Eine Zeile im MixesDB-Stil (ohne Position) — zum Einfügen in Notizen."""
    line = entry.track()
    if entry.label:
        line += f" [{entry.label}"
        if entry.catalog:
            line += f" - {entry.catalog}"
        line += "]"
    return line


def entries_from_lines(lines: Sequence[str], *, page: str = "") -> Setlist:
    """Baut eine Setlist aus freien Zeilen (Notizen, Kopien aus dem Wiki)."""
    entries = tuple(
        entry
        for entry in (parse_entry_line(line) for line in lines)
        if entry is not None
    )
    return Setlist(page=page, entries=entries)
