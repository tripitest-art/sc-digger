"""Library-Audit für bestehende Sammlungen (Phase 2.2).

Prüft bestehende Audio-Archive (/music/Schranz) vollkommen read-only:
- Transcode-/Fake-Erkennung via Spektrums-Cutoff
- Messung von BPM, Camelot-Key, LUFS, True Peak und Dynamikumfang (LRA)
- Inkrementeller Scan: Unveränderte Dateien (mtime + Dateigröße) werden aus der TrackDB übernommen
- Befüllt die zentrale TrackDB (tracks.sqlite) ohne jegliche Modifikation der Audio-Dateien
- Erstellt interaktive HTML- und Konsolen-Reports.
"""
from __future__ import annotations

import html
import logging
import os
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Sequence

if TYPE_CHECKING:
    from .fingerprint import Fingerprint

from . import cues
from .analysis import analyze_track
from .db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from .models import Config
from .organize import _BPM_BUCKET_SIZE, _bpm_bucket
from .quality import check_file
from .rekordbox import (
    _CAMELOT_RE,
    HOT_CUE_LAYOUT_LEGACY,
    KINDS,
    InboxTrack,
    _bpm_sort_key,
    _camelot_sort_key,
    _playlist_folder,
    generate_hot_cues,
    laptop_location,
)
from .style import classify_track, format_compact_rekordbox_comment
from .structure import (
    analyze_structure as analyze_structure_segments,
    calculate_breakdown_ratio,
    find_drop_start,
    find_first_kick,
)

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".wav", ".aiff", ".aif", ".flac", ".mp3", ".m4a"}


@dataclass
class AuditResult:
    path: Path
    status: QualityStatus | str
    reason: str
    format: str | None = None
    bitrate_kbps: float | None = None
    cutoff_hz: int | None = None
    sample_rate: int | None = None
    bpm: float | None = None
    bpm_source: str | None = None
    key_camelot: str | None = None
    key_name: str | None = None
    lufs: float | None = None
    true_peak_dbfs: float | None = None
    loudness_range_lu: float | None = None
    breakdown_ratio: float | None = None
    cached: bool = False


@dataclass
class AuditSummary:
    total_files: int = 0
    scanned: int = 0
    cached: int = 0
    ok: int = 0
    fakes: int = 0
    clipped: int = 0
    corrupt: int = 0
    duration_sec: float = 0.0
    results: list[AuditResult] = field(default_factory=list)
    fingerprints_new: int = 0
    fingerprints_failed: int = 0
    duplicate_groups: list[list[str]] = field(default_factory=list)  # Pfade wie in der DB
    adopted_tracks: list[tuple[str, str]] = field(default_factory=list)  # [(inbox_path, archive_path), ...]


# ================================================================= Tag-Inspektion

def read_existing_tags(path: Path) -> dict[str, Any]:
    """Liest vorhandene Metadaten (BPM, Key, Artist, Title) aus, ohne die Datei zu verändern."""
    meta: dict[str, Any] = {"bpm": None, "key": None, "artist": None, "title": None}
    ext = path.suffix.lower()

    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3, ID3NoHeaderError
            try:
                tags = ID3(str(path))
                if "TBPM" in tags and tags["TBPM"].text:
                    try:
                        meta["bpm"] = float(str(tags["TBPM"].text[0]).strip())
                    except ValueError:
                        pass
                if "TKEY" in tags and tags["TKEY"].text:
                    meta["key"] = str(tags["TKEY"].text[0]).strip()
                if "TPE1" in tags and tags["TPE1"].text:
                    meta["artist"] = str(tags["TPE1"].text[0]).strip()
                if "TIT2" in tags and tags["TIT2"].text:
                    meta["title"] = str(tags["TIT2"].text[0]).strip()
            except ID3NoHeaderError:
                pass

        elif ext == ".flac":
            from mutagen.flac import FLAC
            tags = FLAC(str(path))
            if "bpm" in tags and tags["bpm"]:
                try:
                    meta["bpm"] = float(str(tags["bpm"][0]).strip())
                except ValueError:
                    pass
            for k in ("initialkey", "key"):
                if k in tags and tags[k]:
                    meta["key"] = str(tags[k][0]).strip()
                    break
            if "artist" in tags and tags["artist"]:
                meta["artist"] = str(tags["artist"][0]).strip()
            if "title" in tags and tags["title"]:
                meta["title"] = str(tags["title"][0]).strip()

        elif ext in (".aif", ".aiff"):
            from mutagen.aiff import AIFF
            try:
                tags = AIFF(str(path))
                if tags.tags:
                    id3 = tags.tags
                    if "TBPM" in id3 and id3["TBPM"].text:
                        try:
                            meta["bpm"] = float(str(id3["TBPM"].text[0]).strip())
                        except ValueError:
                            pass
                    if "TKEY" in id3 and id3["TKEY"].text:
                        meta["key"] = str(id3["TKEY"].text[0]).strip()
                    if "TPE1" in id3 and id3["TPE1"].text:
                        meta["artist"] = str(id3["TPE1"].text[0]).strip()
                    if "TIT2" in id3 and id3["TIT2"].text:
                        meta["title"] = str(id3["TIT2"].text[0]).strip()
            except Exception:
                pass

        elif ext == ".m4a":
            from mutagen.mp4 import MP4
            tags = MP4(str(path))
            if "tmpo" in tags and tags["tmpo"]:
                try:
                    meta["bpm"] = float(tags["tmpo"][0])
                except (ValueError, TypeError):
                    pass
            if "\xa9ART" in tags and tags["\xa9ART"]:
                meta["artist"] = str(tags["\xa9ART"][0]).strip()
            if "\xa9nam" in tags and tags["\xa9nam"]:
                meta["title"] = str(tags["\xa9nam"][0]).strip()

    except Exception as e:
        log.debug("Tags konnten für %s nicht gelesen werden: %s", path.name, e)

    return meta


# ================================================================= Einzeldatei-Audit

def audit_file(
    path: Path,
    cfg: Config,
    db: TrackDB,
    *,
    force: bool = False,
    analyze_structure: bool = False,
    write_tags: bool = False,
) -> AuditResult:
    """Prüft eine einzelne Datei gegen Qualitätskriterien und aktualisiert die TrackDB.

    analyze_structure=True ergänzt die rein lesende Strukturanalyse (Kick, Drop, Breakdown)
    und speichert breakdown_ratio; die Audiodatei selbst wird nicht verändert.
    """
    stat = path.stat()
    mtime = stat.st_mtime
    size = stat.st_size

    # Inkrementelle Optimierung: Wenn Datei unverändert und force=False, aus DB laden.
    # Mit analyze_structure wird ein Track ohne gespeicherten breakdown_ratio neu analysiert.
    if not force and not db.needs_audit(path, mtime, size):
        record = db.get_track_by_path(path)
        if record and not (analyze_structure and record.breakdown_ratio is None):
            return AuditResult(
                path=path,
                status=record.quality_status,
                reason=record.quality_details.get("reason", "") if record.quality_details else "",
                format=record.format,
                bitrate_kbps=record.bitrate_kbps,
                cutoff_hz=record.cutoff_hz,
                bpm=record.bpm,
                bpm_source=record.bpm_source,
                key_camelot=record.key_camelot,
                key_name=record.key_name,
                lufs=record.lufs,
                true_peak_dbfs=record.true_peak_dbfs,
                loudness_range_lu=record.loudness_range_lu,
                breakdown_ratio=record.breakdown_ratio,
                cached=True,
            )

    # 1. Qualitäts- & Spektrums-Prüfung
    try:
        report = check_file(path, cfg)
    except Exception as e:
        log.error("Qualitätsprüfung fehlgeschlagen für %s: %s", path.name, e)
        rec = TrackRecord(
            path=str(path),
            mtime=mtime,
            size=size,
            quality_status=QualityStatus.CORRUPT,
            quality_details={"error": str(e)},
            status=TrackStatus.ARCHIVE,
        )
        db.upsert_track(rec)
        return AuditResult(
            path=path,
            status=QualityStatus.CORRUPT,
            reason=f"Audio-Datei defekt oder nicht lesbar: {e}",
        )

    # Bestimmung des Qualitätsstatus
    if not report.get("ok", False):
        status = QualityStatus.FAKE_TRANSCODE
    elif report.get("clipped", False):
        status = QualityStatus.CLIPPED
    else:
        status = QualityStatus.OK

    # 2. Bestehende Tags auslesen
    existing_tags = read_existing_tags(path)
    bpm = existing_tags.get("bpm")
    key_name = existing_tags.get("key")
    key_camelot: str | None = None
    if key_name and _CAMELOT_RE.match(key_name.strip().upper()):
        key_camelot = key_name.strip().upper()
    bpm_source = "tag" if bpm is not None else None

    # Zielfenster für Oktav-Korrektur aus Config (search.bpm_min/max)
    search_cfg = cfg.raw.get("search", {}) if hasattr(cfg, "raw") and isinstance(cfg.raw, dict) else {}
    bpm_min = search_cfg.get("bpm_min", 140.0)
    bpm_max = search_cfg.get("bpm_max", 170.0)
    snap_to = (float(bpm_min), float(bpm_max)) if bpm_min and bpm_max else None

    # Plausibilitätscheck: Liegt der Tag-BPM weit außerhalb des Zielbereichs (< 100 oder > 220),
    # wird er als unplausibel/Oktavfehler eingestuft und per Audio-Analyse korrigiert.
    unplausible_bpm = bpm is not None and (bpm < 100.0 or bpm > 220.0)

    # 3. Audio-Analyse falls BPM oder Key fehlen oder Tag-BPM unplausibel ist
    if bpm is None or key_name is None or unplausible_bpm:
        try:
            analysis = analyze_track(path, snap_to=snap_to)
            if (bpm is None or unplausible_bpm) and analysis.get("bpm"):
                bpm = float(analysis["bpm"])
                bpm_source = "audio"
            if key_name is None:
                key_name = analysis.get("key_name")
                key_camelot = analysis.get("key_camelot")
        except Exception as e:
            log.debug("Audio-Analyse bei Audit übersprungen/fehlgeschlagen für %s: %s", path.name, e)

    # 3a. Tags in Datei schreiben, falls explizit aktiviert (--write-tags)
    if write_tags and status in (QualityStatus.OK, QualityStatus.CLIPPED):
        from .organize import write_tags as do_write_tags
        art = existing_tags.get("artist") or ""
        tit = existing_tags.get("title") or path.stem
        try:
            do_write_tags(
                path,
                artist=art,
                title=tit,
                bpm=bpm,
                key_name=key_name,
                key_camelot=key_camelot,
                genre=existing_tags.get("genre") or "Schranz",
                padding_kb=4,
            )
        except Exception as e:
            log.warning("Tags schreiben fehlgeschlagen für %s: %s", path.name, e)

    # 3b. Strukturanalyse rein lesend (kein Schreiben von ID3-Tags, Goldene Regel 4)
    breakdown_ratio: float | None = None
    if analyze_structure:
        first_kick_s = find_first_kick(path)
        segments = analyze_structure_segments(path)
        breakdown_ratio = calculate_breakdown_ratio(segments) if segments else None
        drop_s = find_drop_start(segments, bpm=bpm, first_kick_s=first_kick_s)
        log.debug("Struktur %s: erster Kick %s s, Drop %s s, Breakdown-Anteil %s",
                  path.name, first_kick_s, drop_s, breakdown_ratio)

    # 4. Speichern in TrackDB
    rec = TrackRecord(
        path=str(path),
        mtime=mtime,
        size=size,
        format=report.get("ext"),
        bitrate_kbps=float(report.get("bitrate_kbps") or 0),
        cutoff_hz=report.get("cutoff_hz"),
        quality_status=status,
        quality_details=report,
        bpm=bpm,
        bpm_source=bpm_source,
        key_camelot=key_camelot,
        key_name=key_name,
        lufs=report.get("integrated_lufs"),
        true_peak_dbfs=report.get("true_peak_dbfs"),
        loudness_range_lu=report.get("loudness_range_lu"),
        artist=existing_tags.get("artist"),
        title=existing_tags.get("title"),
        status=TrackStatus.ARCHIVE,
        breakdown_ratio=breakdown_ratio,
        duration=report.get("duration"),
    )
    db.upsert_track(rec)

    return AuditResult(
        path=path,
        status=status,
        reason=report.get("reason", ""),
        format=report.get("ext"),
        bitrate_kbps=report.get("bitrate_kbps"),
        cutoff_hz=report.get("cutoff_hz"),
        sample_rate=report.get("sample_rate"),
        bpm=bpm,
        bpm_source=bpm_source,
        key_camelot=key_camelot,
        key_name=key_name,
        lufs=report.get("integrated_lufs"),
        true_peak_dbfs=report.get("true_peak_dbfs"),
        loudness_range_lu=report.get("loudness_range_lu"),
        breakdown_ratio=breakdown_ratio,
        cached=False,
    )


# ================================================================= Sammlungs-Audit

def audit_collection(
    collection_dir: str | Path,
    cfg: Config,
    db: TrackDB,
    *,
    force: bool = False,
    analyze_structure: bool = False,
    write_tags: bool = False,
) -> AuditSummary:
    """Führt ein vollständiges Audit über den angegebenen Sammlungs-Ordner aus."""
    folder = Path(collection_dir)
    if not folder.exists():
        raise FileNotFoundError(f"Sammlungsordner existiert nicht: {folder}")

    t0 = time.time()
    files = sorted(
        [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS],
        key=lambda p: str(p).lower(),
    )

    summary = AuditSummary(total_files=len(files))
    log.info("Starte Audit über %d Audio-Dateien in %s", len(files), folder)

    for i, file_path in enumerate(files, 1):
        if i % 25 == 0 or i == len(files):
            log.info("Audit Fortschritt: %d/%d Dateien...", i, len(files))

        res = audit_file(
            file_path,
            cfg,
            db,
            force=force,
            analyze_structure=analyze_structure,
            write_tags=write_tags,
        )
        summary.results.append(res)

        if res.cached:
            summary.cached += 1
        else:
            summary.scanned += 1

        st = res.status.value if hasattr(res.status, "value") else str(res.status)
        if st == QualityStatus.OK.value:
            summary.ok += 1
        elif st == QualityStatus.FAKE_TRANSCODE.value:
            summary.fakes += 1
        elif st == QualityStatus.CLIPPED.value:
            summary.clipped += 1
        elif st == QualityStatus.CORRUPT.value:
            summary.corrupt += 1

    summary.duration_sec = time.time() - t0
    return summary


# ================================================================= Report-Generierung

def generate_html_report(summary: AuditSummary, target_dir: str | Path) -> str:
    """Erzeugt ein ansprechendes HTML-Audit-Dashboard für den DJ."""
    folder_str = str(target_dir)

    rows_html: list[str] = []
    for r in summary.results:
        st = r.status.value if hasattr(r.status, "value") else str(r.status)
        if st == QualityStatus.OK.value:
            badge = '<span class="badge badge-ok">OK</span>'
        elif st == QualityStatus.FAKE_TRANSCODE.value:
            badge = '<span class="badge badge-fake">FAKE TRANSCODE</span>'
        elif st == QualityStatus.CLIPPED.value:
            badge = '<span class="badge badge-clipped">BRICKWALL</span>'
        else:
            badge = f'<span class="badge badge-corrupt">{html.escape(st.upper())}</span>'

        cutoff_str = f"{r.cutoff_hz / 1000:.1f} kHz" if r.cutoff_hz else "—"
        bpm_str = f"{r.bpm:.1f}" if r.bpm else "—"
        key_str = r.key_camelot or r.key_name or "—"
        lufs_str = f"{r.lufs:.1f} LUFS" if r.lufs is not None else "—"
        tp_str = f"{r.true_peak_dbfs:+.1f} dBTP" if r.true_peak_dbfs is not None else "—"
        bitrate_str = f"{r.bitrate_kbps:.0f}k" if r.bitrate_kbps else "—"
        cached_tag = ' <span class="tag-cached">cached</span>' if r.cached else ""

        rows_html.append(
            f"""<tr>
                <td><strong>{html.escape(r.path.name)}</strong>{cached_tag}<br>
                    <small class="text-muted">{html.escape(str(r.path))}</small>
                </td>
                <td>{badge}</td>
                <td>{html.escape(r.format or "")} ({bitrate_str})</td>
                <td>{cutoff_str}</td>
                <td>{bpm_str}</td>
                <td>{html.escape(key_str)}</td>
                <td>{lufs_str} / {tp_str}</td>
                <td><small>{html.escape(r.reason or "")}</small></td>
            </tr>"""
        )

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>sc-digger Library Audit Report</title>
<style>
  :root {{
    --bg: #121214;
    --card: #1c1c1f;
    --text: #e4e4e7;
    --muted: #a1a1aa;
    --border: #27272a;
    --ok: #22c55e;
    --fake: #ef4444;
    --clipped: #f59e0b;
    --corrupt: #ec4899;
    --accent: #3b82f6;
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    margin: 0;
    padding: 2rem;
  }}
  .container {{ max-width: 1400px; margin: 0 auto; }}
  h1 {{ margin: 0 0 0.5rem 0; font-size: 1.8rem; font-weight: 700; }}
  p.subtitle {{ color: var(--muted); margin: 0 0 2rem 0; }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 1rem;
    margin-bottom: 2rem;
  }}
  .stat-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1.2rem;
  }}
  .stat-value {{ font-size: 2rem; font-weight: 700; margin-bottom: 0.2rem; }}
  .stat-label {{ color: var(--muted); font-size: 0.85rem; text-transform: uppercase; }}
  .val-ok {{ color: var(--ok); }}
  .val-fake {{ color: var(--fake); }}
  .val-clipped {{ color: var(--clipped); }}
  .val-cached {{ color: var(--accent); }}
  table {{
    width: 100%;
    border-collapse: collapse;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }}
  th, td {{
    padding: 0.85rem 1rem;
    text-align: left;
    border-bottom: 1px solid var(--border);
  }}
  th {{
    background: #18181b;
    font-size: 0.8rem;
    color: var(--muted);
    text-transform: uppercase;
  }}
  tr:hover {{ background: rgba(255, 255, 255, 0.02); }}
  .text-muted {{ color: var(--muted); }}
  .badge {{
    font-size: 0.75rem;
    font-weight: 600;
    padding: 0.25rem 0.5rem;
    border-radius: 4px;
    display: inline-block;
  }}
  .badge-ok {{ background: rgba(34, 197, 94, 0.15); color: var(--ok); }}
  .badge-fake {{ background: rgba(239, 68, 68, 0.15); color: var(--fake); }}
  .badge-clipped {{ background: rgba(245, 158, 11, 0.15); color: var(--clipped); }}
  .badge-corrupt {{ background: rgba(236, 72, 153, 0.15); color: var(--corrupt); }}
  .tag-cached {{
    font-size: 0.65rem;
    background: rgba(59, 130, 246, 0.15);
    color: var(--accent);
    padding: 0.1rem 0.35rem;
    border-radius: 3px;
  }}
</style>
</head>
<body>
<div class="container">
  <h1>🎧 sc-digger Library Audit Report</h1>
  <p class="subtitle">Pfad: <code>{html.escape(folder_str)}</code> | Dauer: {summary.duration_sec:.1f} s</p>

  <div class="grid">
    <div class="stat-card">
      <div class="stat-value">{summary.total_files}</div>
      <div class="stat-label">Tracks Gesamt</div>
    </div>
    <div class="stat-card">
      <div class="stat-value val-ok">{summary.ok}</div>
      <div class="stat-label">Echte Qualität</div>
    </div>
    <div class="stat-card">
      <div class="stat-value val-fake">{summary.fakes}</div>
      <div class="stat-label">Fake Transcodes</div>
    </div>
    <div class="stat-card">
      <div class="stat-value val-clipped">{summary.clipped}</div>
      <div class="stat-label">Brickwall Clipped</div>
    </div>
    <div class="stat-card">
      <div class="stat-value val-cached">{summary.cached}</div>
      <div class="stat-label">Aus Cache</div>
    </div>
  </div>

  <table>
    <thead>
      <tr>
        <th>Track</th>
        <th>Status</th>
        <th>Format / Bitrate</th>
        <th>Cutoff</th>
        <th>BPM</th>
        <th>Key</th>
        <th>LUFS / Peak</th>
        <th>Hinweis</th>
      </tr>
    </thead>
    <tbody>
      {"".join(rows_html)}
    </tbody>
  </table>
</div>
</body>
</html>
"""


def fill_fingerprints(
    db: TrackDB,
    *,
    compute: Callable[[Path], Fingerprint | None] | None = None,
) -> tuple[int, int]:
    """Für jeden Track aus db.tracks_missing_fingerprint(), dessen Datei existiert: compute(Path(path)).
    compute=None -> sc_digger.fingerprint.compute_fingerprint, zur Laufzeit über das Modul
    nachgeschlagen (Tests ersetzen fingerprint.compute_fingerprint). Ergebnis per
    db.set_fingerprint(id, encode_fingerprint(fp.values), fp.duration) speichern.
    Nicht existierende Dateien: überspringen, nicht zählen. None oder Exception: zählt als
    fehlgeschlagen, log.warning, nie werfen. Rückgabe (neu, fehlgeschlagen).
    """
    from . import fingerprint as fpm

    missing = db.tracks_missing_fingerprint()
    new_count = 0
    failed_count = 0

    for track in missing:
        p = Path(track.path)
        if not p.is_file():
            continue

        calc_fn = compute if compute is not None else getattr(fpm, "compute_fingerprint")
        try:
            fp = calc_fn(p)
            if fp is None or not getattr(fp, "values", None) or getattr(fp, "duration", None) is None:
                log.warning("Fingerprint-Berechnung lieferte kein Ergebnis für %s", p)
                failed_count += 1
                continue

            encoded = fpm.encode_fingerprint(fp.values)
            ok = db.set_fingerprint(track.id, encoded, fp.duration)
            if ok:
                new_count += 1
            else:
                failed_count += 1
        except Exception as e:
            log.warning("Fehler bei Fingerprint-Berechnung für %s: %s", p, e)
            failed_count += 1

    return new_count, failed_count


def mark_adopted_tracks(
    db: TrackDB,
    *,
    now: datetime | str | None = None,
) -> list[tuple[TrackRecord, TrackRecord]]:
    """Gleicht nicht-übernommene Inbox-Tracks (status='inbox', adopted_at IS NULL)
    per Audio-Fingerprint gegen archivierte Tracks (status='archive') ab.

    - Verwendet sc_digger.fingerprint.same_recording zur Erkennung gleicher Aufnahmen.
    - Kandidatensuche in der DB per db.fingerprint_candidates(duration, MAX_DURATION_DIFF_S),
      gefiltert auf status in (TrackStatus.ARCHIVE, 'archive').
    - Bei Treffer: Zeitstempel setzen, db.mark_adopted(inbox_rec.id, ts) aufrufen
      (überschreibt ein dislike nicht) und das Paar (inbox_rec, archive_rec) merken.
    - Bereits übernommene Tracks (adopted_at IS NOT NULL) werden ignoriert (idempotent).
    - Berührt Dateien auf der Festplatte nicht (weder Inbox noch Sammlung; Goldene Regel 4).
    - Rückgabe: Liste von Tupeln (inbox_rec, archive_rec) aller in diesem Aufruf neu
      übernommenen Tracks.
    """
    from . import fingerprint as fpm
    from .fingerprint import Fingerprint, MAX_DURATION_DIFF_S

    # Zeitstempel einmal bestimmen: now (str) unverändert, datetime formatiert, sonst UTC-jetzt.
    if isinstance(now, str):
        ts = now
    elif isinstance(now, datetime):
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
    else:
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # same_recording zur Laufzeit nachschlagen, damit Tests es ersetzen können.
    same_fn = getattr(fpm, "same_recording", fpm.same_recording)

    # Nur unübernommene Inbox-Tracks betrachten (idempotent gegenüber adopted_at IS NOT NULL).
    candidates = db.list_tracks(status=TrackStatus.INBOX)
    inbox_records = [r for r in candidates if r.adopted_at is None]

    adopted: list[tuple[TrackRecord, TrackRecord]] = []
    for inbox_rec in inbox_records:
        if not inbox_rec.fingerprint or inbox_rec.fingerprint_duration is None:
            continue

        inbox_fp = Fingerprint(
            values=fpm.decode_fingerprint(inbox_rec.fingerprint),
            duration=inbox_rec.fingerprint_duration,
        )

        match: TrackRecord | None = None
        for cand in db.fingerprint_candidates(
            inbox_rec.fingerprint_duration, MAX_DURATION_DIFF_S
        ):
            cand_status = cand.status.value if hasattr(cand.status, "value") else cand.status
            if cand_status != TrackStatus.ARCHIVE.value:
                continue
            if cand.id == inbox_rec.id:
                continue
            if not cand.fingerprint or cand.fingerprint_duration is None:
                continue
            cand_fp = Fingerprint(
                values=fpm.decode_fingerprint(cand.fingerprint),
                duration=cand.fingerprint_duration,
            )
            if same_fn(inbox_fp, cand_fp):
                match = cand
                break

        if match is None:
            continue

        db.mark_adopted(inbox_rec.id, ts, archive_track_id=match.id)
        refreshed = db.get_track_by_id(inbox_rec.id)
        adopted.append((refreshed or inbox_rec, match))

    return adopted


def _record_to_inbox_track(rec: TrackRecord) -> InboxTrack:
    """Wandelt einen TrackRecord aus der TrackDB in eine InboxTrack für den XML-Export um.

    Pfad bleibt der Server-Pfad; die Übersetzung auf die Laptop-Location passiert später
    über laptop_location(path_map). Dauer und LUFS kommen aus der TrackDB (duration None ->
    0.0), damit TotalTime und der kompakte Kommentar dieselben Werte wie die Inbox tragen.
    Die Cue-Zeitpunkte und die Breakdown-Art stammen aus den Cue-Tags der Datei; die TrackDB
    speichert keine Cue-Zeitpunkte.
    """
    p = Path(rec.path)
    track = InboxTrack(
        path=p,
        title=rec.title or p.stem,
        artist=rec.artist or "",
        genre="",
        bpm=rec.bpm,
        key=rec.key_camelot or rec.key_name or "",
        comment="",
        duration_s=float(rec.duration) if rec.duration is not None else 0.0,
        size=int(rec.size) if rec.size else 0,
        added=datetime.fromtimestamp(rec.mtime) if rec.mtime else datetime.fromtimestamp(0),
    )
    track.first_kick_s = cues.read_first_kick_tag(p)
    track.drop_s = cues.read_drop_tag(p)
    track.breakdown_s = cues.read_breakdown_tag(p)
    track.breakdown_kind = cues.read_breakdown_kind_tag(p)
    track.drop2_s = cues.read_drop2_tag(p)
    track.lufs = rec.lufs
    return track


def _append_cue_marks(
    track_node: ET.Element,
    track: InboxTrack,
    *,
    hot_cues: bool,
    memory_cues: bool,
    hot_cue_layout: str = HOT_CUE_LAYOUT_LEGACY,
    phrase_align: bool = False,
) -> None:
    """Hängt Hot- und Memory-Cues als <POSITION_MARK> an den TRACK-Knoten (wie build_xml).

    Chronologisch nach Start; bei gleichem Zeitstempel steht der Hot Cue vor dem Memory Cue.
    hot_cue_layout/phrase_align werden an generate_hot_cues durchgereicht.
    """
    marks: list[tuple[float, int, str, str, tuple[str, str, str] | None]] = []
    for start_s, pad, name, color in generate_hot_cues(
        track, layout=hot_cue_layout, phrase_align=phrase_align
    ):
        if hot_cues:
            marks.append((start_s, 0, name, pad, color))
        if memory_cues:
            marks.append((start_s, 1, name, "-1", None))

    marks.sort(key=lambda m: (m[0], m[1]))
    for start_s, _is_memory, name, num, color in marks:
        mark_attr = {"Name": name, "Type": "0", "Start": f"{start_s:.3f}", "Num": num}
        if color is not None:
            mark_attr["Red"], mark_attr["Green"], mark_attr["Blue"] = color
        ET.SubElement(track_node, "POSITION_MARK", mark_attr)


def export_collection_xml(
    db: TrackDB,
    xml_path: str | Path,
    path_map: dict[str, str],
    *,
    collection_dir: str | Path | None = None,
    playlists_by_bpm: bool = True,
    playlists_by_key: bool = True,
    playlists_by_version: bool = False,
    hot_cues: bool = True,
    memory_cues: bool = True,
    hot_cue_layout: str = HOT_CUE_LAYOUT_LEGACY,
    phrase_align_cues: bool = False,
    grid_anchor: bool = False,
    compact_style_comments: bool = False,
) -> int:
    """Liest TrackRecords mit status='archive' aus db.

    Überspringt Records mit quality_status in ('fake_transcode', 'corrupt').
    Wandelt gültige Records in InboxTrack-Instanzen um (Pfade, Metadaten, BPM, Key, Dauer, LUFS).
    Liegt xml_path innerhalb von collection_dir: wirft ValueError (Goldene Regel 4).
    Schreibt die Rekordbox-XML atomar (wie write_rekordbox_xml).
    Gibt die Anzahl der erfolgreich in die XML geschriebenen Tracks zurück.

    hot_cue_layout/phrase_align_cues steuern die Cue-Erzeugung wie in build_xml; grid_anchor
    schreibt den Beatgrid-Anker <TEMPO> als erstes TRACK-Kind (first_kick_s >= 0, bpm > 0);
    compact_style_comments füllt Comments mit dem kompakten Display-Kommentar. Die
    Standardwerte erhalten das bisherige Verhalten (legacy, kein Anker, leeres Comments).
    """
    target = Path(xml_path)

    # Die bestehende Sammlung bleibt streng read-only: kein Schreiben in collection_dir.
    if collection_dir is not None:
        coll_dir = Path(collection_dir).resolve()
        target_resolved = target.resolve()
        if target_resolved == coll_dir or target_resolved.is_relative_to(coll_dir):
            raise ValueError(
                f"XML-Pfad darf nicht im Sammlungsordner liegen: {xml_path} liegt in {coll_dir}"
            )

    records = db.list_tracks(status=TrackStatus.ARCHIVE)
    skip_quality = {QualityStatus.FAKE_TRANSCODE.value, QualityStatus.CORRUPT.value}

    tracks: list[InboxTrack] = []
    for rec in records:
        q = rec.quality_status.value if hasattr(rec.quality_status, "value") else rec.quality_status
        if q in skip_quality:
            continue
        tracks.append(_record_to_inbox_track(rec))

    # Gruppierung/Export über dieselbe Logik wie build_xml, aber ohne Wochen-Playlists.
    mapped: list[tuple[str, InboxTrack, str]] = []
    for t in tracks:
        loc = laptop_location(t.path, path_map)
        if loc is not None:
            track_id = str(len(mapped) + 1)
            mapped.append((track_id, t, loc))

    root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
    ET.SubElement(root, "PRODUCT", {"Name": "rekordbox", "Version": "6.0.0", "Company": "Pioneer DJ"})

    coll = ET.SubElement(root, "COLLECTION", {"Entries": str(len(mapped))})
    for track_id, t, loc in mapped:
        attr = {
            "TrackID": track_id,
            "Name": t.title,
            "Artist": t.artist,
            "Genre": t.genre,
            "Kind": KINDS.get(t.path.suffix.lower(), ""),
            "Size": str(t.size),
            "TotalTime": str(int(round(t.duration_s))),
            "DateAdded": t.added.strftime("%Y-%m-%d"),
            "Comments": t.comment,
            "Tonality": t.key,
            "Location": loc,
        }
        if t.bpm is not None:
            attr["AverageBpm"] = f"{t.bpm:.2f}"
        if compact_style_comments:
            analysis = classify_track(bpm=t.bpm, title=t.title)
            attr["Comments"] = format_compact_rekordbox_comment(
                analysis,
                bpm=t.bpm,
                key_camelot=t.key or None,
                drop1_s=t.drop_s,
                drop2_s=t.drop2_s,
                lufs=t.lufs,
            )
        track_node = ET.SubElement(coll, "TRACK", attr)
        # Beatgrid-Anker vor den Cue-Marken, damit er erstes TRACK-Kind bleibt (wie build_xml).
        if grid_anchor and t.first_kick_s is not None and t.bpm is not None:
            kick_s = float(t.first_kick_s)
            anchor_bpm = float(t.bpm)
            if kick_s >= 0 and anchor_bpm > 0:
                ET.SubElement(track_node, "TEMPO", {
                    "Beginning": f"{kick_s:.3f}",
                    "Bpm": f"{anchor_bpm:.2f}",
                    "Metro": "4/4",
                    "Battito": "1",
                })
        _append_cue_marks(
            track_node,
            t,
            hot_cues=hot_cues,
            memory_cues=memory_cues,
            hot_cue_layout=hot_cue_layout,
            phrase_align=phrase_align_cues,
        )

    playlists = ET.SubElement(root, "PLAYLISTS")
    top = ET.SubElement(playlists, "NODE", {"Type": "0", "Name": "ROOT", "Count": "1"})
    folder = ET.SubElement(top, "NODE", {"Type": "0", "Name": "sc-digger", "Count": "0"})

    extra_folders = 0

    if playlists_by_bpm:
        bpm_dict: dict[str, list[str]] = {}
        for track_id, t, _ in mapped:
            label = _bpm_bucket(t.bpm, _BPM_BUCKET_SIZE)
            if label == _bpm_bucket(None, _BPM_BUCKET_SIZE):
                label = "unbekannt"
            bpm_dict.setdefault(label, []).append(track_id)
        if bpm_dict:
            groups = sorted(bpm_dict.items(), key=lambda kv: _bpm_sort_key(kv[0]))
            _playlist_folder(folder, "BPM", groups)
            extra_folders += 1

    if playlists_by_key:
        key_dict: dict[str, list[str]] = {}
        for track_id, t, _ in mapped:
            name = t.key.strip().upper()
            label = name if _CAMELOT_RE.match(name) else "unbekannt"
            key_dict.setdefault(label, []).append(track_id)
        if key_dict:
            groups = sorted(key_dict.items(), key=lambda kv: _camelot_sort_key(kv[0]))
            _playlist_folder(folder, "Key", groups)
            extra_folders += 1

    if playlists_by_version:
        from .rekordbox import VERSION_TYPE_PLAYLISTS
        from .style import classify_track
        version_dict: dict[Any, list[str]] = {}
        for track_id, t, _ in mapped:
            analysis = classify_track(bpm=t.bpm, title=t.title)
            version_dict.setdefault(analysis.version_type, []).append(track_id)
        groups = [(name, version_dict[vt]) for vt, name in VERSION_TYPE_PLAYLISTS if version_dict.get(vt)]
        if groups:
            _playlist_folder(folder, "Versionen", groups)
            extra_folders += 1

    folder.set("Count", str(extra_folders))

    xml_content = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".", suffix=".xml.tmp")
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(xml_content)
        os.replace(tmp_path, target)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise

    log.info("Sammlungs-XML geschrieben: %s (%d Tracks)", target, len(mapped))
    return len(mapped)


def run_audit(
    cfg: Config,
    path: str | Path | None = None,
    report_path: str | Path | None = None,
    *,
    force: bool = False,
    dry_run: bool = False,
    no_telegram: bool = True,
    export_xml_path: str | Path | None = None,
    check_adopted: bool = False,
    analyze_structure: bool = False,
    write_tags: bool = False,
) -> AuditSummary:
    """Einstiegspunkt für den CLI-Befehl `audit`."""
    target_dir = Path(path or cfg["download"]["collection_dir"]).resolve()

    # Report darf niemals im Sammlungsordner abgelegt werden (Sammlung ist strikt read-only)
    if report_path:
        r_path = Path(report_path).resolve()
        checked_dirs = [target_dir]
        if "download" in cfg.raw and "collection_dir" in cfg["download"]:
            try:
                checked_dirs.append(Path(cfg["download"]["collection_dir"]).resolve())
            except Exception:
                pass
        for cdir in checked_dirs:
            try:
                r_path.relative_to(cdir)
                raise ValueError(
                    f"Report-Pfad darf nicht im Sammlungsordner liegen: {report_path} liegt in {cdir}"
                )
            except ValueError as e:
                if "Report-Pfad darf nicht" in str(e):
                    raise

    db_path = cfg["state"]["track_db_path"]

    with TrackDB(db_path) as db:
        # Nur bei Bedarf weiterreichen: bestehende Aufrufer mit der alten Signatur
        # (z. B. Test-Fakes für audit_collection) bleiben unverändert gültig.
        if analyze_structure or write_tags:
            summary = audit_collection(
                target_dir,
                cfg,
                db,
                force=force,
                analyze_structure=analyze_structure,
                write_tags=write_tags,
            )
        else:
            summary = audit_collection(target_dir, cfg, db, force=force)
        from .fingerprint import duplicate_groups

        summary.fingerprints_new, summary.fingerprints_failed = fill_fingerprints(db)
        summary.duplicate_groups = [
            [r.path for r in g]
            for g in duplicate_groups(db.tracks_with_fingerprint())
        ]

        if check_adopted:
            pairs = mark_adopted_tracks(db)
            summary.adopted_tracks = [(inb.path, arc.path) for inb, arc in pairs]

        if export_xml_path:
            coll_dir: str | None = None
            try:
                coll_dir = cfg["download"]["collection_dir"]
            except (KeyError, TypeError):
                coll_dir = None
            # Optionen wie in write_rekordbox_xml aus cfg["rekordbox"] lesen; fehlender
            # Abschnitt/Schlüssel -> Standardwert, damit audit --export-xml dasselbe Layout,
            # denselben Anker und dieselben Cue-Arten wie der Inbox-Export nutzt.
            rb_cfg = cfg.raw.get("rekordbox") if isinstance(cfg.raw, dict) else None
            if not isinstance(rb_cfg, dict):
                rb_cfg = {}
            hot_cues = rb_cfg.get("cues_as_hotcues", True)
            hot_cues = True if hot_cues is None else bool(hot_cues)
            memory_cues = rb_cfg.get("cues_as_memory", True)
            memory_cues = True if memory_cues is None else bool(memory_cues)
            raw_layout: Any = rb_cfg.get("hot_cue_layout", HOT_CUE_LAYOUT_LEGACY)
            hot_cue_layout = raw_layout.strip() if isinstance(raw_layout, str) else ""
            if not hot_cue_layout:
                hot_cue_layout = HOT_CUE_LAYOUT_LEGACY
            grid_anchor = rb_cfg.get("grid_anchor", True)
            grid_anchor = True if grid_anchor is None else bool(grid_anchor)
            count = export_collection_xml(
                db,
                export_xml_path,
                rb_cfg.get("path_map", {}),
                collection_dir=coll_dir,
                hot_cues=hot_cues,
                memory_cues=memory_cues,
                hot_cue_layout=hot_cue_layout,
                phrase_align_cues=bool(rb_cfg.get("cues_phrase_align", False)),
                grid_anchor=grid_anchor,
                compact_style_comments=bool(rb_cfg.get("compact_style_comments", False)),
                playlists_by_bpm=bool(rb_cfg.get("playlists_by_bpm", True)),
                playlists_by_key=bool(rb_cfg.get("playlists_by_key", True)),
                playlists_by_version=bool(rb_cfg.get("playlists_by_version", False)),
            )
            print(f"\n🎛️  Sammlungs-XML geschrieben: {Path(export_xml_path).resolve()} ({count} Tracks)")

    # Terminal-Ausgabe
    print("\n" + "=" * 60)
    print(f"📊 sc-digger Library Audit Report: {target_dir}")
    print("=" * 60)
    print(f"Tracks Gesamt:     {summary.total_files}")
    print(f"✅ Echte Qualität: {summary.ok}")
    print(f"🚨 Fake-Transcodes:{summary.fakes}")
    print(f"⚠️  Brickwall/Clip: {summary.clipped}")
    print(f"❌ Defekt/Corrupt: {summary.corrupt}")
    print(f"⚡ Aus Cache:      {summary.cached} ({summary.scanned} neu analysiert)")
    print(f"🎵 Fingerprints:   {summary.fingerprints_new} neu ({summary.fingerprints_failed} fehlgeschlagen)")
    print(f"⏱️  Dauer:          {summary.duration_sec:.1f} s")
    print("=" * 60)

    # Wenn Fakes gefunden wurden, diese prominent listen
    fakes = [
        r
        for r in summary.results
        if (r.status.value if hasattr(r.status, "value") else str(r.status))
        == QualityStatus.FAKE_TRANSCODE.value
    ]
    if fakes:
        print("\n🚨 GEFUNDENE FAKE-TRANSCODES / UPSCALES:")
        for f in fakes:
            cutoff_khz = f.cutoff_hz / 1000 if f.cutoff_hz else 0
            print(f" - {f.path.name} ({f.format} {f.bitrate_kbps:.0f}k, Cutoff {cutoff_khz:.1f} kHz)")
            print(f"   Grund: {f.reason}")

    # Doppelte Aufnahmen
    if summary.duplicate_groups:
        print("\n👥 DOPPELTE AUFNAHMEN:")
        for idx, group in enumerate(summary.duplicate_groups, start=1):
            print(f" Gruppe {idx} ({len(group)} Dateien):")
            for p in group:
                print(f"  - {p}")

    # Übernommene Inbox-Tracks (ROADMAP 3.12)
    if check_adopted:
        print(f"\n✅ ÜBERNOMMENE TRACKS: {len(summary.adopted_tracks)} neu in der Sammlung erkannt")
        for inbox_path, archive_path in summary.adopted_tracks:
            print(f" - {inbox_path}")
            print(f"   -> {archive_path}")

    # HTML-Report speichern, falls Pfad angegeben
    if report_path:
        r_path = Path(report_path)
        r_path.parent.mkdir(parents=True, exist_ok=True)
        html_content = generate_html_report(summary, target_dir)
        r_path.write_text(html_content, encoding="utf-8")
        print(f"\n📄 HTML-Report geschrieben nach: {r_path.resolve()}")

    return summary
