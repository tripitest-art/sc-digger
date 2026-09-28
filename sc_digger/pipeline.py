"""Filter-Pipeline: BPM, Scoring (perzentil-basiert), Download-Klassifizierung."""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from .models import Config, DownloadKind, Track

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")

GATE_HOSTS = {
    "hypeddit.com": DownloadKind.HYPEDDIT,
    "droploud.com": DownloadKind.DROPLOUD,
    "toneden.io": DownloadKind.TONEDEN,
    "theartistunion.com": DownloadKind.ARTIST_UNION,
}
STORE_HOSTS = ("bandcamp.com", "beatport.com", "traxsource.com", "juno.co.uk")
CLOUD_HOSTS = ("drive.google.com", "dropbox.com", "mega.nz", "mega.io", "wetransfer.com")


def _host(url: str) -> str:
    try:
        h = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return h.lower().removeprefix("www.")


def _host_matches(host: str, needle: str) -> bool:
    return host == needle or host.endswith("." + needle)


# ---------------------------------------------------------------- BPM
BPM_IN_TEXT = re.compile(r"(?<!\d)(1[0-9]{2})\s*bpm\b", re.I)


def estimate_bpm(t: Track) -> float | None:
    """SoundCloud hat kein BPM-Feld. Titel, Tags, Beschreibung nach 'xxx bpm' durchsuchen."""
    hay = " ".join([t.title, " ".join(t.tags), t.description[:600]])
    m = BPM_IN_TEXT.search(hay)
    return float(m.group(1)) if m else None


def filter_bpm(tracks: list[Track], cfg: Config) -> list[Track]:
    lo, hi = cfg["search"]["bpm_min"], cfg["search"]["bpm_max"]
    policy = cfg["search"]["bpm_unknown_policy"]
    out = []
    for t in tracks:
        t.bpm = estimate_bpm(t)
        if t.bpm is None:
            if policy == "keep":
                t.notes.append("BPM unbekannt")
                out.append(t)
        elif lo <= t.bpm <= hi:
            out.append(t)
    return out


# ---------------------------------------------------------------- Dedup
def dedupe(tracks: list[Track]) -> list[Track]:
    seen: dict[int, Track] = {}
    for t in tracks:
        seen.setdefault(t.id, t)
    return list(seen.values())


# ---------------------------------------------------------------- Scoring
def _percentile_rank(value: float, sorted_vals: list[float]) -> float:
    """Anteil der Werte, die <= value sind (0-100)."""
    if not sorted_vals:
        return 0.0
    import bisect
    return 100.0 * bisect.bisect_right(sorted_vals, value) / len(sorted_vals)


def score_tracks(tracks: list[Track], cfg: Config) -> list[Track]:
    """Bewertet relativ zur aktuellen Kandidatenmenge (Genre-Baseline).

    Absolute Schwellen wie 'Like-Ratio > 5 %' filtern bei Schranz/Hard Techno fast
    alles weg, weil Plays durch Autoplay aufgebläht sind. Perzentile sind robuster.
    """
    sc = cfg["scoring"]
    pool = [t for t in tracks if t.plays >= sc["min_plays"]]
    if len(pool) < 10:
        log.warning("Nur %d Kandidaten über min_plays -> Perzentile wenig aussagekräftig", len(pool))

    like_sorted = sorted(t.like_ratio for t in pool)
    rep_sorted = sorted(t.repost_ratio for t in pool)
    com_sorted = sorted(t.comment_ratio for t in pool)
    w = sc["weights"]
    now = datetime.now(timezone.utc)
    max_age = cfg["search"]["max_age_days"]

    for t in pool:
        age_days = (now - datetime.fromisoformat(t.created_at.replace("Z", "+00:00"))).days
        recency = max(0.0, 1.0 - age_days / max_age)
        # log-Dämpfung: sehr viele Plays sollen nicht automatisch gewinnen
        t.score = (
            w["like_ratio"] * _percentile_rank(t.like_ratio, like_sorted)
            + w["repost_ratio"] * _percentile_rank(t.repost_ratio, rep_sorted)
            + w["comment_ratio"] * _percentile_rank(t.comment_ratio, com_sorted)
            + w["recency"] * 100.0 * recency
        )
    scores = sorted(t.score for t in pool)
    for t in pool:
        t.percentile = _percentile_rank(t.score, scores)

    keep = [
        t for t in pool
        if t.like_ratio >= sc["min_like_ratio"] and t.percentile >= sc["min_percentile"]
    ]
    keep.sort(key=lambda t: t.score, reverse=True)
    return keep


# ---------------------------------------------------------------- Download-Klassifizierung
def classify_download(t: Track) -> Track:
    """Bestimmt den Download-Weg. Gates werden nur erkannt, nie durchlaufen."""
    if t.downloadable and t.has_downloads_left:
        t.download_kind = DownloadKind.NATIVE
        t.download_link = t.url
        return t

    candidates: list[str] = []
    if t.purchase_url:
        candidates.append(t.purchase_url)
    candidates += URL_RE.findall(t.description or "")

    store_hit = cloud_hit = None
    for u in candidates:
        h = _host(u)
        for gate_host, kind in GATE_HOSTS.items():
            if _host_matches(h, gate_host):
                t.download_kind, t.download_link = kind, u
                return t
        if not store_hit and any(_host_matches(h, s) for s in STORE_HOSTS):
            store_hit = u
        if not cloud_hit and any(_host_matches(h, c) for c in CLOUD_HOSTS):
            cloud_hit = u

    if cloud_hit:
        t.download_kind, t.download_link = DownloadKind.CLOUD, cloud_hit
    elif store_hit:
        t.download_kind, t.download_link = DownloadKind.STORE, store_hit
    else:
        t.download_kind, t.download_link = DownloadKind.NONE, None
    return t
