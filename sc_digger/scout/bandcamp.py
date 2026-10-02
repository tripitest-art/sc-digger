"""Bandcamp-RSS-Scout: liest Label-Feeds und erzeugt Kaufempfehlungen als Tracks.

Bandcamp bietet für Labels und Artists RSS-Feeds an, die neue Einträge im Shop
auflisten. Diese sind reine Empfehlungen – es wird nichts geladen, nur verlinkt
(DownloadKind.STORE, Regel 6). Ein kaputter Feed darf die übrigen nicht
blockieren (Regel 7): Fehler werden geloggt.
"""
from __future__ import annotations

import hashlib
import logging
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime

import requests

from sc_digger.models import DownloadKind, Track

log = logging.getLogger(__name__)

# Bandcamp nutzt Dublin Core für Künstler, Genre und Tags.
_DC_NS = "http://purl.org/dc/elements/1.1/"


def _text(item: ET.Element, tag: str) -> str:
    """Erster Textinhalt zum Tag, leerer String wenn nicht vorhanden."""
    found = item.find(tag)
    if found is None or found.text is None:
        return ""
    return found.text.strip()


def _parse_date(raw: str) -> str:
    """Wandelt ein RSS-pubDate in einen ISO-8601-String; sonst heutiges Datum."""
    if raw:
        try:
            return parsedate_to_datetime(raw).astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            pass
    return datetime.now(timezone.utc).date().isoformat()


def _track_id(purchase_url: str) -> int:
    """Stabile ID aus der Bandcamp-URL, da es keine SoundCloud-ID gibt.

    SoundCloud-IDs sind große Ganzzahlen; der Sha1-Ausschnitt bleibt im
    positiven 63-Bit-Bereich, damit sich die Werte nicht beißen.
    """
    digest = hashlib.sha1(purchase_url.encode("utf-8")).hexdigest()
    return int(digest[:15], 16)


def _parse_rss(xml_text: str, feed_url: str) -> list[Track]:
    """Extrahiert alle Tracks aus einem Bandcamp-RSS-Dokument.

    Items ohne <link> werden übersprungen (kein Fehler). Ein ungültiges XML
    wirft – der Aufrufer entscheidet, ob das den ganzen Lauf stoppt.
    """
    root = ET.fromstring(xml_text)
    tracks: list[Track] = []
    for item in root.iter("item"):
        link = _text(item, "link")
        if not link:
            continue
        tag_values = [
            t.text.strip()
            for t in item.findall(f"{{{_DC_NS}}}subject")
            if t.text and t.text.strip()
        ]
        tracks.append(
            Track(
                id=_track_id(link),
                title=_text(item, "title"),
                url=link,
                artist=_text(item, f"{{{_DC_NS}}}creator"),
                artist_url="",
                created_at=_parse_date(_text(item, "pubDate")),
                duration_ms=0,
                genre=_text(item, f"{{{_DC_NS}}}genre"),
                tags=tag_values,
                description=_text(item, "description"),
                bpm=None,
                plays=0,
                likes=0,
                reposts=0,
                comments=0,
                downloadable=False,
                has_downloads_left=False,
                purchase_url=link,
                purchase_title="Bandcamp",
                download_kind=DownloadKind.STORE,
            )
        )
    return tracks


def fetch_bandcamp_feeds(feed_urls: list[str], timeout: int = 20) -> list[Track]:
    """Lädt RSS-Feeds von Bandcamp-Labels und gibt Tracks als Kaufempfehlungen zurück.

    Fehler bei einzelnen Feeds (Timeout, ungültiges XML) werden geloggt, aber
    nicht propagiert – andere Feeds werden trotzdem verarbeitet.
    """
    tracks: list[Track] = []
    for url in feed_urls:
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            tracks.extend(_parse_rss(response.text, feed_url=url))
        except Exception as exc:  # noqa: BLE001 – ein Feed darf den Lauf nicht stoppen
            log.warning("Bandcamp-Feed %s nicht lesbar: %s", url, exc)
    return tracks
