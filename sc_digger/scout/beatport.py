"""Beatport-Chart-Scout: scraped Chartseiten und erzeugt Kaufempfehlungen als Tracks.

Beatport veröffentlicht Top-100-Charts je Genre. Diese sind reine Empfehlungen –
es wird nichts geladen, nur verlinkt (DownloadKind.STORE, Regel 6). Ein kaputtes
Chart darf die übrigen nicht blockieren (Regel 7): Fehler werden geloggt.

Bewusst reines HTML-Scraping mit BeautifulSoup (html.parser) – kein headless
Browser und keine Beatport-API (kein API-Key, kein OAuth).
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date

import requests
from bs4 import BeautifulSoup

from sc_digger.models import DownloadKind, Track

log = logging.getLogger(__name__)

# Beatport liefert Chrome keinen Track-Link als Text, daher holen wir uns die
# URL aus dem umschließenden <a>. Die Zeilen tragen die Klasse `track`.
_TRACK_SELECTOR = "div.bucket-item.track, div.track"


def _track_id(purchase_url: str) -> int:
    """Stabile ID aus der Beatport-URL, da es keine SoundCloud-ID gibt.

    SoundCloud-IDs sind große Ganzzahlen; der Sha1-Ausschnitt bleibt im
    positiven 63-Bit-Bereich, damit sich die Werte nicht beißen.
    """
    digest = hashlib.sha1(purchase_url.encode("utf-8")).hexdigest()
    return int(digest[:15], 16)


def _parse_chart_html(html: str, chart_url: str) -> list[Track]:
    """Extrahiert alle Tracks aus einer Beatport-Chartseite.

    Zeilen ohne gültigen Track-Link werden übersprungen (kein Fehler). Fehlt
    Titel oder Künstler im HTML, wird der Wert leer gelassen.
    """
    soup = BeautifulSoup(html, "html.parser")
    tracks: list[Track] = []
    for item in soup.select(_TRACK_SELECTOR):
        link = item.find("a", href=True)
        if link is None:
            continue
        purchase_url = link["href"].strip()
        if not purchase_url:
            continue
        title_el = item.select_one(".buk-track-title")
        artist_el = item.select_one(".buk-track-artists")
        title = title_el.get_text(strip=True) if title_el else ""
        artist = artist_el.get_text(strip=True) if artist_el else ""
        tracks.append(
            Track(
                id=_track_id(purchase_url),
                title=title,
                url=purchase_url,
                artist=artist,
                artist_url="",
                created_at=date.today().isoformat(),
                duration_ms=0,
                genre="",
                tags=[],
                description="",
                bpm=None,
                plays=0,
                likes=0,
                reposts=0,
                comments=0,
                downloadable=False,
                has_downloads_left=False,
                purchase_url=purchase_url,
                purchase_title="Beatport",
                download_kind=DownloadKind.STORE,
            )
        )
    return tracks


def fetch_beatport_charts(chart_urls: list[str], timeout: int = 20) -> list[Track]:
    """Lädt Beatport-Chartseiten, extrahiert Tracks und gibt sie als Kaufempfehlungen zurück.

    Fehler bei einzelnen Charts (Timeout, HTTP-Fehler) werden geloggt, aber
    nicht propagiert – andere Charts werden trotzdem verarbeitet.
    """
    tracks: list[Track] = []
    for url in chart_urls:
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            tracks.extend(_parse_chart_html(response.text, chart_url=url))
        except Exception as exc:  # noqa: BLE001 – ein Chart darf den Lauf nicht stoppen
            log.warning("Beatport-Chart %s nicht lesbar: %s", url, exc)
    return tracks
