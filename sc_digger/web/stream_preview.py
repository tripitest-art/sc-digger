"""Hörproben für das Web-Dashboard aus dem SoundCloud-Stream (Befund 8, Teil 1).

Ein Schnipsel (OGG-Opus, 30 s) entsteht erst, wenn jemand ihn abspielen will, und liegt
danach in einem Zwischenspeicher neben der State-DB. Der Speicher ist begrenzt; die ältesten
Dateien fliegen zuerst raus. Eigener Schalter `web.stream_preview` (Standard aus), getrennt
vom Bot-Schalter `preview.stream_enabled`.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..models import Config
from ..preview import extract_preview_from_url
from ..soundcloud import SoundCloudClient

log = logging.getLogger(__name__)

DEFAULT_CACHE_MB = 500
SNIPPET_SECONDS = 30.0


def resolve_stream_url(sc_id: int) -> str | None:
    """Stream-URL des Tracks bei SoundCloud (Netz); jede Ausnahme -> None."""
    try:
        return SoundCloudClient().preview_url(sc_id)
    except Exception as e:
        # Bewusst ohne Fehlertext: SoundCloud-Fehler können URLs mit client_id enthalten.
        log.warning("Stream-URL für Hörprobe nicht ermittelbar (%s)", type(e).__name__)
        return None


def _web(cfg: Config) -> dict:
    web = cfg.raw.get("web") if isinstance(cfg.raw, dict) else None
    return web if isinstance(web, dict) else {}


def preview_enabled(cfg: Config) -> bool:
    """Ist `web.stream_preview` an? Fehlt der Schlüssel: aus."""
    return bool(_web(cfg).get("stream_preview", False))


def cache_dir(cfg: Config) -> Path:
    """Zwischenspeicher `preview_cache` neben der State-DB (gleiches Volume, gleiche Sicherung)."""
    return Path(cfg["state"]["db_path"]).parent / "preview_cache"


def cache_limit_bytes(cfg: Config) -> int:
    """Obergrenze des Zwischenspeichers in Bytes; fehlt/ungültig: 500 MB."""
    value = _web(cfg).get("preview_cache_mb", DEFAULT_CACHE_MB)
    # bool ist ein int in Python, soll hier aber nicht als „1 MB“ durchgehen.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        value = DEFAULT_CACHE_MB
    return int(value) * 1024 * 1024


def prune_cache(root: Path, max_bytes: int) -> int:
    """Löscht *.ogg in root (älteste zuerst, bei Gleichstand nach Name), bis die Summe
    <= max_bytes ist. Rückgabe: Anzahl gelöschter Dateien. Wirft nie."""
    try:
        files = []
        for p in Path(root).glob("*.ogg"):
            try:
                if p.is_file() and not p.is_symlink():
                    st = p.stat()
                    files.append((st.st_mtime, p.name, st.st_size, p))
            except OSError:
                continue
        total = sum(f[2] for f in files)
        deleted = 0
        # Nur *.ogg zählen und löschen: was sonst im Ordner liegt, gehört nicht uns.
        for _mtime, _name, size, path in sorted(files, key=lambda f: (f[0], f[1])):
            if total <= max_bytes:
                break
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
            deleted += 1
        return deleted
    except Exception as e:
        log.warning("Hörproben-Zwischenspeicher nicht aufräumbar: %s", e)
        return 0


def get_or_build_preview(cfg: Config, sc_id: int) -> Path | None:
    """Pfad der Hörprobe für sc_id, bei Bedarf neu erzeugt; None, wenn keine möglich ist.

    Schalter aus oder ungültige ID: sofort None, ohne Netz. Wirft nie.
    """
    try:
        # bool ist ein int; True wäre sonst die ID 1.
        if isinstance(sc_id, bool) or not isinstance(sc_id, int) or sc_id <= 0:
            return None
        if not preview_enabled(cfg):
            return None
        root = cache_dir(cfg)
        # Der Dateiname besteht nur aus der geprüften Zahl: nichts außerhalb des Ordners.
        ziel = root / f"{sc_id}.ogg"
        if ziel.is_file() and ziel.stat().st_size > 0:
            return ziel
        url = resolve_stream_url(sc_id)
        if not url:
            return None
        # Ordner selbst anlegen, statt sich auf den Extraktor zu verlassen (Tests ersetzen ihn).
        root.mkdir(parents=True, exist_ok=True)
        if not extract_preview_from_url(
            url, ziel, start_s=0.0, duration_s=SNIPPET_SECONDS, bitrate_kbps=64
        ):
            return None
        prune_cache(root, cache_limit_bytes(cfg))
        return ziel if ziel.is_file() else None
    except Exception as e:
        log.warning("Hörprobe nicht erzeugbar: %s", e)
        return None
