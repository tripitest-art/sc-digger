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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Sequence

if TYPE_CHECKING:
    from .fingerprint import Fingerprint

from .analysis import analyze_track
from .db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from .models import Config
from .quality import check_file

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
) -> AuditResult:
    """Prüft eine einzelne Datei gegen Qualitätskriterien und aktualisiert die TrackDB."""
    stat = path.stat()
    mtime = stat.st_mtime
    size = stat.st_size

    # Inkrementelle Optimierung: Wenn Datei unverändert und force=False, aus DB laden
    if not force and not db.needs_audit(path, mtime, size):
        record = db.get_track_by_path(path)
        if record:
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
    bpm_source = "tag" if bpm is not None else None

    # 3. Audio-Analyse falls BPM oder Key fehlen
    if bpm is None or key_name is None:
        try:
            analysis = analyze_track(path)
            if bpm is None and analysis.get("bpm"):
                bpm = float(analysis["bpm"])
                bpm_source = "audio"
            if key_name is None:
                key_name = analysis.get("key_name")
                key_camelot = analysis.get("key_camelot")
        except Exception as e:
            log.debug("Audio-Analyse bei Audit übersprungen/fehlgeschlagen für %s: %s", path.name, e)

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
        cached=False,
    )


# ================================================================= Sammlungs-Audit

def audit_collection(
    collection_dir: str | Path,
    cfg: Config,
    db: TrackDB,
    *,
    force: bool = False,
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

        res = audit_file(file_path, cfg, db, force=force)
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


def run_audit(
    cfg: Config,
    path: str | Path | None = None,
    report_path: str | Path | None = None,
    *,
    force: bool = False,
    dry_run: bool = False,
    no_telegram: bool = True,
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
        summary = audit_collection(
            target_dir,
            cfg,
            db,
            force=force,
        )
        from .fingerprint import duplicate_groups

        summary.fingerprints_new, summary.fingerprints_failed = fill_fingerprints(db)
        summary.duplicate_groups = [
            [r.path for r in g]
            for g in duplicate_groups(db.tracks_with_fingerprint())
        ]

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

    # HTML-Report speichern, falls Pfad angegeben
    if report_path:
        r_path = Path(report_path)
        r_path.parent.mkdir(parents=True, exist_ok=True)
        html_content = generate_html_report(summary, target_dir)
        r_path.write_text(html_content, encoding="utf-8")
        print(f"\n📄 HTML-Report geschrieben nach: {r_path.resolve()}")

    return summary
