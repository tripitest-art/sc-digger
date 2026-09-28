"""Library-Audit für bestehende Sammlungen (Phase 2.2).

Prüft bestehende Audio-Archive (/music/Schranz) vollkommen read-only:
- Transcode-/Fake-Erkennung via Spektrums-Cutoff
- Messung von BPM, Camelot-Key, LUFS, True Peak und Dynamikumfang (LRA)
- Inkrementeller Scan: Unveränderte Dateien (mtime + Dateigröße) werden aus der TrackDB übernommen
- Opt-In Tag-Backfill (--backfill-tags): Ergänzt ausschließlich leere Felder (BPM/Key),
  überschreibt niemals bestehende Tags (z.B. aus Rekordbox oder Mixed In Key).
- Erstellt interaktive HTML- und Konsolen-Reports.
"""
from __future__ import annotations

import html
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

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
    backfilled: list[str] = field(default_factory=list)


@dataclass
class AuditSummary:
    total_files: int = 0
    scanned: int = 0
    cached: int = 0
    ok: int = 0
    fakes: int = 0
    clipped: int = 0
    corrupt: int = 0
    backfilled: int = 0
    duration_sec: float = 0.0
    results: list[AuditResult] = field(default_factory=list)


# ================================================================= Tag-Inspektion & Backfill

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


def backfill_empty_tags(
    path: Path,
    *,
    existing_tags: dict[str, Any],
    bpm: float | None = None,
    key_name: str | None = None,
) -> list[str]:
    """Schreibt NUR dann Tags, wenn das entsprechende Tag in der Datei noch leer ist.

    Bestehende Tags (z.B. aus Rekordbox oder Mixed In Key) werden NIEMALS überschrieben!
    """
    ext = path.suffix.lower()
    backfilled: list[str] = []
    need_bpm = bpm is not None and existing_tags.get("bpm") is None
    need_key = key_name is not None and existing_tags.get("key") is None

    if not (need_bpm or need_key):
        return backfilled

    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3, TBPM, TKEY, ID3NoHeaderError
            try:
                tags = ID3(str(path))
            except ID3NoHeaderError:
                tags = ID3()
            if need_bpm:
                tags.add(TBPM(encoding=3, text=[f"{bpm:.0f}"]))
                backfilled.append("BPM")
            if need_key:
                tags.add(TKEY(encoding=3, text=[str(key_name)]))
                backfilled.append("Key")
            tags.save(str(path))

        elif ext == ".flac":
            from mutagen.flac import FLAC
            tags = FLAC(str(path))
            if need_bpm:
                tags["bpm"] = [f"{bpm:.0f}"]
                backfilled.append("BPM")
            if need_key:
                tags["initialkey"] = [str(key_name)]
                backfilled.append("Key")
            tags.save()

        elif ext in (".aif", ".aiff"):
            from mutagen.aiff import AIFF
            from mutagen.id3 import TBPM, TKEY
            tags = AIFF(str(path))
            if tags.tags is None:
                tags.add_tags()
            if need_bpm:
                tags.tags.add(TBPM(encoding=3, text=[f"{bpm:.0f}"]))
                backfilled.append("BPM")
            if need_key:
                tags.tags.add(TKEY(encoding=3, text=[str(key_name)]))
                backfilled.append("Key")
            tags.save()

        elif ext == ".m4a":
            from mutagen.mp4 import MP4
            tags = MP4(str(path))
            if need_bpm:
                tags["tmpo"] = [int(round(bpm))]
                backfilled.append("BPM")
            tags.save()

    except Exception as e:
        log.warning("Tag-Backfill fehlgeschlagen für %s: %s", path.name, e)

    return backfilled


# ================================================================= Einzeldatei-Audit

def audit_file(
    path: Path,
    cfg: Config,
    db: TrackDB,
    *,
    backfill_tags: bool = False,
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

    # 4. Opt-in Tag-Backfill
    backfilled: list[str] = []
    if backfill_tags:
        backfilled = backfill_empty_tags(
            path,
            existing_tags=existing_tags,
            bpm=bpm,
            key_name=key_name,
        )

    # 5. Speichern in TrackDB
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
        backfilled=backfilled,
    )


# ================================================================= Sammlungs-Audit

def audit_collection(
    collection_dir: str | Path,
    cfg: Config,
    db: TrackDB,
    *,
    backfill_tags: bool = False,
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

        res = audit_file(file_path, cfg, db, backfill_tags=backfill_tags, force=force)
        summary.results.append(res)

        if res.cached:
            summary.cached += 1
        else:
            summary.scanned += 1

        if res.backfilled:
            summary.backfilled += 1

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
        backfilled_tag = (
            f' <span class="tag-backfilled">+{",".join(r.backfilled)}</span>'
            if r.backfilled
            else ""
        )

        rows_html.append(
            f"""<tr>
                <td><strong>{html.escape(r.path.name)}</strong>{cached_tag}{backfilled_tag}<br>
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
  .tag-backfilled {{
    font-size: 0.65rem;
    background: rgba(168, 85, 247, 0.15);
    color: #c084fc;
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


def run_audit(
    cfg: Config,
    path: str | Path | None = None,
    report_path: str | Path | None = None,
    *,
    backfill_tags: bool = False,
    force: bool = False,
    dry_run: bool = False,
    no_telegram: bool = True,
) -> AuditSummary:
    """Einstiegspunkt für den CLI-Befehl `audit`."""
    target_dir = Path(path or cfg["download"]["collection_dir"])
    db_path = cfg.raw.get("state", {}).get("db_path", "/data/seen.sqlite")

    with TrackDB(db_path) as db:
        summary = audit_collection(
            target_dir,
            cfg,
            db,
            backfill_tags=backfill_tags and not dry_run,
            force=force,
        )

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
    if backfill_tags:
        print(f"🏷️  Tags ergänzt:   {summary.backfilled}")
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

    # HTML-Report speichern, falls Pfad angegeben
    if report_path:
        r_path = Path(report_path)
        r_path.parent.mkdir(parents=True, exist_ok=True)
        html_content = generate_html_report(summary, target_dir)
        r_path.write_text(html_content, encoding="utf-8")
        print(f"\n📄 HTML-Report geschrieben nach: {r_path.resolve()}")

    return summary
