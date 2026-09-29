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
CLOUD_HOSTS = ("drive.google.com", "dropbox.com")
WETRANSFER_HOSTS = ("wetransfer.com", "we.tl")
MEGA_HOSTS = ("mega.nz", "mega.co.nz", "mega.io")

DOWNLOAD_PRIORITY: tuple[DownloadKind, ...] = (
    DownloadKind.NATIVE,
    DownloadKind.CLOUD,
    DownloadKind.STORE,
    DownloadKind.WETRANSFER,
    DownloadKind.MEGA,
    DownloadKind.HYPEDDIT,
    DownloadKind.DROPLOUD,
    DownloadKind.TONEDEN,
    DownloadKind.ARTIST_UNION,
    DownloadKind.NONE,
)


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


# ---------------------------------------------------------------- DJ-Sets
def is_dj_set(t: Track, max_minutes: float) -> bool:
    """True genau dann, wenn t.duration_ms > max_minutes * 60_000 (strikt größer).
    Unbekannte Dauer (0 oder None) -> False. Titelwörter („mix“, „set“, „session“ …)
    werden NICHT ausgewertet."""
    if not t.duration_ms or t.duration_ms <= 0:
        return False
    return t.duration_ms > max_minutes * 60_000


def is_mix_title(t: Track, patterns: list[str]) -> bool:
    """True, wenn eines der Regex-Muster (re.search, re.IGNORECASE) auf t.title passt.
    t.title None -> "". Ungültiges Muster: log.warning mit dem Muster, überspringen, nie werfen.
    Leere Liste -> False."""
    if not patterns or t is None:
        return False
    title = t.title or ""
    for pat in patterns:
        try:
            if re.search(pat, title, re.IGNORECASE):
                return True
        except re.error:
            log.warning("Ungültiges Regex-Muster für Mix-Titel: %r", pat)
            continue
    return False



def filter_sets(tracks: list[Track], cfg: Config) -> list[Track]:
    """Nur für discover: neue Liste ohne DJ-Sets, Reihenfolge bleibt.
    Grenze: cfg["search"].get("max_duration_min", 12).
    Wurde mindestens einer entfernt: log.info("DJ-Sets aussortiert: %d (länger als %s min)", anzahl, grenze)"""
    limit = cfg["search"].get("max_duration_min", 12)
    kept = [t for t in tracks if not is_dj_set(t, limit)]
    removed = len(tracks) - len(kept)
    if removed > 0:
        log.info("DJ-Sets aussortiert: %d (länger als %s min)", removed, limit)
    patterns = cfg["search"].get("set_title_patterns") or []
    if patterns:
        mix_kept = [t for t in kept if not is_mix_title(t, patterns)]
        mix_removed = len(kept) - len(mix_kept)
        if mix_removed > 0:
            log.info("Mix-Titel aussortiert: %d", mix_removed)
        kept = mix_kept
    return kept



def mark_sets(tracks: list[Track], cfg: Config) -> list[Track]:
    """Für alle Modi: entfernt nichts. Für jedes DJ-Set t.set_minutes = round(t.duration_ms / 60_000),
    andere Tracks bleiben unverändert. Gibt dieselbe Liste zurück."""
    limit = cfg["search"].get("max_duration_min", 12)
    for t in tracks:
        if is_dj_set(t, limit):
            t.set_minutes = round(t.duration_ms / 60_000)
    return tracks


# ---------------------------------------------------------------- Genre-Relevanz
def genre_relevant(t: Track, keywords: list[str]) -> bool:
    """Prüft Titel/Genre/Tags auf eines der Suchwörter.

    Für reference_activity() nötig: Reposts/Likes von Referenz-Accounts sind sonst
    ungefiltert deren gesamte Aktivität (Trance, House, Boiler-Room-Sets, ...), nicht
    nur der Schranz/Hard-Techno-Ausschnitt, an dem die Reference-Boost-Logik interessiert ist.
    """
    hay = " ".join([t.title, t.genre, " ".join(t.tags)]).lower()
    return any(k.lower() in hay for k in keywords)


# ---------------------------------------------------------------- Dedup
def dedupe(tracks: list[Track]) -> list[Track]:
    seen: dict[int, Track] = {}
    for t in tracks:
        seen.setdefault(t.id, t)
    return list(seen.values())


# ---------------------------------------------------------------- Spam-Signale & Scoring
def spam_signals(t: Track, phrases: list[str]) -> list[str]:
    """Gründe (Klartext), warum t nach Promo-Netzwerk aussieht; [] wenn unauffällig. Feste Reihenfolge:
    1. "Reposts > 3× Likes"                 wenn t.reposts >= 10 und t.reposts > 3 * t.likes
    2. "viele Reposts, kaum Kommentare"     wenn t.reposts >= 50 und t.comments <= 1
    3. "Promo-Text: <phrase in Kleinbuchstaben>" für die ERSTE Phrase aus phrases, die (Groß/klein egal)
       in t.title, t.description oder einem Eintrag von t.tags vorkommt; höchstens eine solche Zeile.
    None-Felder zählen als leer. Wirft nicht."""
    reasons: list[str] = []
    try:
        reposts = t.reposts or 0
        likes = t.likes or 0
        comments = t.comments or 0

        if reposts >= 10 and reposts > 3 * likes:
            reasons.append("Reposts > 3× Likes")

        if reposts >= 50 and comments <= 1:
            reasons.append("viele Reposts, kaum Kommentare")

        title = (t.title or "").lower()
        desc = (t.description or "").lower()
        tags = [str(tag).lower() for tag in (t.tags or []) if tag]

        for p in phrases:
            if not p:
                continue
            p_lower = p.strip().lower()
            if p_lower in title or p_lower in desc or any(p_lower in tag for tag in tags):
                reasons.append(f"Promo-Text: {p_lower}")
                break
    except Exception as e:
        log.warning("Fehler in spam_signals: %s", e)

    return reasons


def _percentile_rank(value: float, sorted_vals: list[float]) -> float:
    """Anteil der Werte, die <= value sind (0-100)."""
    if not sorted_vals:
        return 0.0
    import bisect
    return 100.0 * bisect.bisect_right(sorted_vals, value) / len(sorted_vals)


def score_tracks(tracks: list[Track], cfg: Config, apply_filter: bool = True) -> list[Track]:
    """Bewertet relativ zur aktuellen Kandidatenmenge (Genre-Baseline).

    Absolute Schwellen wie 'Like-Ratio > 5 %' filtern bei Schranz/Hard Techno fast
    alles weg, weil Plays durch Autoplay aufgebläht sind. Perzentile sind robuster.

    apply_filter=False: nur bewerten und nach Score sortieren, nichts aussortieren.
    Für On-Demand-Checks (Playlist/Station) will man die volle Liste sehen, nicht
    nur das obere Perzentil des täglichen Digests.
    """
    sc = cfg["scoring"]
    pool = [t for t in tracks if t.plays >= sc["min_plays"]] if apply_filter else list(tracks)
    if len(pool) < 10:
        log.warning("Nur %d Kandidaten über min_plays -> Perzentile wenig aussagekräftig", len(pool))

    like_sorted = sorted(t.like_ratio for t in pool)
    rep_sorted = sorted(t.repost_ratio for t in pool)
    com_sorted = sorted(t.comment_ratio for t in pool)
    w = sc["weights"]
    now = datetime.now(timezone.utc)
    max_age = cfg["search"]["max_age_days"]

    for t in pool:
        try:
            age_days = (now - datetime.fromisoformat(t.created_at.replace("Z", "+00:00"))).days
        except (ValueError, TypeError):
            age_days = max_age
        recency = max(0.0, 1.0 - age_days / max_age)
        # log-Dämpfung: sehr viele Plays sollen nicht automatisch gewinnen
        t.score = (
            w["like_ratio"] * _percentile_rank(t.like_ratio, like_sorted)
            + w["repost_ratio"] * _percentile_rank(t.repost_ratio, rep_sorted)
            + w["comment_ratio"] * _percentile_rank(t.comment_ratio, com_sorted)
            + w["recency"] * 100.0 * recency
        )
        if t.reference_hit:
            t.score += sc.get("reference_boost", 0)
        reasons = spam_signals(t, sc.get("spam_phrases") or [])
        if reasons:
            t.score -= sc.get("spam_penalty", 0)
            t.notes.append("Promo-Verdacht: " + "; ".join(reasons))
    scores = sorted(t.score for t in pool)
    for t in pool:
        t.percentile = _percentile_rank(t.score, scores)

    if apply_filter:
        pool = [
            t for t in pool
            if t.like_ratio >= sc["min_like_ratio"] and t.percentile >= sc["min_percentile"]
        ]
    pool.sort(key=lambda t: t.score, reverse=True)
    return pool


# ---------------------------------------------------------------- Download-Klassifizierung
def classify_url(url: str) -> tuple[DownloadKind, str]:
    """Klassifiziert eine einzelne URL anhand von Host und Pfad."""
    h = _host(url)
    for gate_host, kind in GATE_HOSTS.items():
        if _host_matches(h, gate_host):
            return kind, url
    if any(_host_matches(h, w) for w in WETRANSFER_HOSTS):
        return DownloadKind.WETRANSFER, url
    if any(_host_matches(h, m) for m in MEGA_HOSTS):
        return DownloadKind.MEGA, url
    if any(_host_matches(h, s) for s in STORE_HOSTS):
        return DownloadKind.STORE, url
    if any(_host_matches(h, c) for c in CLOUD_HOSTS):
        return DownloadKind.CLOUD, url
    return DownloadKind.NONE, url


def classify_download(t_or_url: Track | str) -> Track | tuple[DownloadKind, str]:
    """Bestimmt den Download-Weg. Gates werden nur erkannt, nie durchlaufen.

    Kann entweder mit einem Track-Objekt (Pipeline) oder einer URL als String
    aufgerufen werden.
    """
    if isinstance(t_or_url, str):
        return classify_url(t_or_url)

    t = t_or_url
    if t.downloadable and t.has_downloads_left:
        t.download_kind = DownloadKind.NATIVE
        t.download_link = t.url
        return t

    candidates: list[str] = []
    if t.purchase_url:
        candidates.append(t.purchase_url)
    candidates += URL_RE.findall(t.description or "")

    store_hit = cloud_hit = wetransfer_hit = mega_hit = None
    for u in candidates:
        h = _host(u)
        for gate_host, kind in GATE_HOSTS.items():
            if _host_matches(h, gate_host):
                t.download_kind, t.download_link = kind, u
                return t
        if not cloud_hit and any(_host_matches(h, c) for c in CLOUD_HOSTS):
            cloud_hit = u
        if not store_hit and any(_host_matches(h, s) for s in STORE_HOSTS):
            store_hit = u
        if not wetransfer_hit and any(_host_matches(h, w) for w in WETRANSFER_HOSTS):
            wetransfer_hit = u
        if not mega_hit and any(_host_matches(h, m) for m in MEGA_HOSTS):
            mega_hit = u

    if cloud_hit:
        t.download_kind, t.download_link = DownloadKind.CLOUD, cloud_hit
    elif store_hit:
        t.download_kind, t.download_link = DownloadKind.STORE, store_hit
    elif wetransfer_hit:
        t.download_kind, t.download_link = DownloadKind.WETRANSFER, wetransfer_hit
    elif mega_hit:
        t.download_kind, t.download_link = DownloadKind.MEGA, mega_hit
    else:
        t.download_kind, t.download_link = DownloadKind.NONE, None
    return t
