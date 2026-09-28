"""Dünner Client für die inoffizielle SoundCloud api-v2.

Hinweise:
- Es gibt keinen offiziellen öffentlichen Zugang für neue Apps. Die client_id
  wird aus dem Frontend-JS von soundcloud.com gelesen und wechselt gelegentlich.
- Es werden nur öffentliche Metadaten gelesen. Kein Login, keine Downloads hier.
- Wenn diese Schicht bricht, ist sie die einzige Stelle, die angepasst werden muss.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Iterator

import requests

from .models import Track

log = logging.getLogger(__name__)

API = "https://api-v2.soundcloud.com"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class SoundCloudError(RuntimeError):
    pass


class SoundCloudClient:
    def __init__(self, request_delay: float = 0.6):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.client_id: str | None = None
        self.delay = request_delay

    # ---------- client_id ----------
    def _fetch_client_id(self) -> str:
        html = self.s.get("https://soundcloud.com", timeout=20).text
        scripts = re.findall(r'<script crossorigin src="(https://[^"]+\.js)"', html)
        for url in reversed(scripts):
            js = self.s.get(url, timeout=20).text
            m = re.search(r'client_id\s*[:=]\s*"([a-zA-Z0-9]{32})"', js)
            if m:
                return m.group(1)
        raise SoundCloudError("client_id konnte nicht ermittelt werden")

    def _ensure_client_id(self) -> None:
        if not self.client_id:
            self.client_id = self._fetch_client_id()
            log.info("client_id ermittelt")

    # ---------- HTTP ----------
    def _get(self, path_or_url: str, params: dict | None = None) -> dict:
        self._ensure_client_id()
        url = path_or_url if path_or_url.startswith("http") else f"{API}{path_or_url}"
        params = dict(params or {})
        params["client_id"] = self.client_id
        for attempt in range(3):
            r = self.s.get(url, params=params, timeout=20)
            if r.status_code == 401 or r.status_code == 403:
                # client_id abgelaufen -> neu holen
                self.client_id = None
                self._ensure_client_id()
                params["client_id"] = self.client_id
                continue
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            time.sleep(self.delay)
            return r.json()
        raise SoundCloudError(f"Request fehlgeschlagen: {url}")

    def _paginate(self, path: str, params: dict, limit: int) -> Iterator[dict]:
        page = self._get(path, {**params, "limit": min(limit, 50)})
        seen = 0
        while True:
            for item in page.get("collection", []):
                yield item
                seen += 1
                if seen >= limit:
                    return
            nxt = page.get("next_href")
            if not nxt:
                return
            page = self._get(nxt)

    # ---------- Mapping ----------
    @staticmethod
    def _to_track(d: dict) -> Track | None:
        # Playlist-Stubs oder gelöschte Tracks überspringen
        if d.get("kind") != "track" or not d.get("title"):
            return None
        user = d.get("user") or {}
        tag_list = d.get("tag_list") or ""
        tags = re.findall(r'"([^"]+)"|(\S+)', tag_list)
        tags = [a or b for a, b in tags]
        return Track(
            id=d["id"],
            title=d["title"],
            url=d.get("permalink_url", ""),
            artist=user.get("username", ""),
            artist_url=user.get("permalink_url", ""),
            created_at=d.get("created_at", ""),
            duration_ms=d.get("full_duration") or d.get("duration") or 0,
            genre=d.get("genre") or "",
            tags=tags,
            description=d.get("description") or "",
            bpm=None,  # SoundCloud liefert kein verlässliches BPM-Feld
            plays=d.get("playback_count") or 0,
            likes=d.get("likes_count") or d.get("favoritings_count") or 0,
            reposts=d.get("reposts_count") or 0,
            comments=d.get("comment_count") or 0,
            downloadable=bool(d.get("downloadable")),
            has_downloads_left=bool(d.get("has_downloads_left", True)),
            purchase_url=d.get("purchase_url"),
            purchase_title=d.get("purchase_title"),
        )

    # ---------- öffentliche API ----------
    def search_tag(self, tag: str, max_age_days: int, limit: int) -> list[Track]:
        """Neueste Tracks zu einem Tag. Filter created_at.to/from serverseitig."""
        since = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        params = {
            "q": tag,
            "filter.created_at": "last_week" if max_age_days <= 7 else "last_month",
            "sort": "recent",
        }
        out: list[Track] = []
        for raw in self._paginate("/search/tracks", params, limit):
            t = self._to_track(raw)
            if not t:
                continue
            created = datetime.fromisoformat(t.created_at.replace("Z", "+00:00"))
            if created < since:
                continue
            out.append(t)
        log.info("Tag %r: %d Tracks", tag, len(out))
        return out

    def user_uploads(self, profile_url: str, max_age_days: int, limit: int = 30) -> list[Track]:
        info = self._get("/resolve", {"url": profile_url})
        uid = info.get("id")
        if not uid:
            log.warning("Profil nicht auflösbar: %s", profile_url)
            return []
        since = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        out: list[Track] = []
        for raw in self._paginate(f"/users/{uid}/tracks", {}, limit):
            t = self._to_track(raw)
            if not t:
                continue
            created = datetime.fromisoformat(t.created_at.replace("Z", "+00:00"))
            if created < since:
                continue
            out.append(t)
        return out

    # ---------- Playlists ----------
    def _hydrate_stubs(self, items: list[dict]) -> list[dict]:
        """Playlists liefern viele Tracks nur als Stub (nur id/kind). Per /tracks?ids= nachladen."""
        full = [i for i in items if i.get("title")]
        stub_ids = [i["id"] for i in items if not i.get("title") and i.get("id")]
        for i in range(0, len(stub_ids), 30):
            chunk = stub_ids[i:i + 30]
            data = self._get("/tracks", {"ids": ",".join(map(str, chunk))})
            # /tracks?ids= liefert eine Liste, kein {"collection": ...}
            full += data if isinstance(data, list) else data.get("collection", [])
        return full

    def playlist_tracks(self, playlist_url: str) -> tuple[str, list[Track]]:
        """Alle Tracks einer Playlist (in Playlist-Reihenfolge). Gibt (Titel, Tracks) zurück."""
        info = self._get("/resolve", {"url": playlist_url})
        kind = info.get("kind")
        if kind not in ("playlist", "system-playlist"):
            raise SoundCloudError(f"Keine Playlist-URL (kind={kind!r}): {playlist_url}")
        title = info.get("title", "Playlist")
        raw = info.get("tracks", [])
        by_id = {r["id"]: r for r in self._hydrate_stubs(raw)}
        ordered = [by_id[r["id"]] for r in raw if r.get("id") in by_id]
        tracks = [t for t in (self._to_track(r) for r in ordered) if t]
        log.info("Playlist %r: %d von %d Tracks lesbar", title, len(tracks), len(raw))
        return title, tracks

    def likes_of_user(self, profile_url: str, limit: int = 200) -> tuple[str, list[Track]]:
        """Likes eines Profils wie eine Playlist behandeln."""
        info = self._get("/resolve", {"url": profile_url})
        if info.get("kind") != "user":
            raise SoundCloudError(f"Keine Profil-URL: {profile_url}")
        out = []
        for raw in self._paginate(f"/users/{info['id']}/track_likes", {}, limit):
            t = self._to_track(raw.get("track") or raw)
            if t:
                out.append(t)
        return f"Likes von {info.get('username', '?')}", out

    # ---------- Empfehlungen zu einem Track ----------
    def resolve_track(self, track_url: str) -> Track:
        info = self._get("/resolve", {"url": track_url})
        t = self._to_track(info)
        if not t:
            raise SoundCloudError(f"Kein Track: {track_url}")
        return t

    def related(self, track_id: int, limit: int = 50) -> list[Track]:
        """SoundClouds 'Related Tracks' – der Algorithmus hinter dem Track-Radio."""
        out = []
        for raw in self._paginate(f"/tracks/{track_id}/related", {}, limit):
            t = self._to_track(raw)
            if t:
                out.append(t)
        log.info("Related zu %s: %d Tracks", track_id, len(out))
        return out

    def station(self, track_id: int, limit: int = 50) -> list[Track]:
        """Track-Radio (Station). Fällt auf related() zurück, falls der Endpunkt nicht antwortet."""
        try:
            page = self._get(f"/system-playlists/soundcloud:system-playlists:track-stations:{track_id}")
            raw = page.get("tracks", [])
            tracks = [t for t in (self._to_track(r) for r in self._hydrate_stubs(raw)) if t]
            if tracks:
                return tracks[:limit]
        except Exception as e:
            log.info("Station nicht verfügbar (%s) -> related()", e)
        return self.related(track_id, limit)
