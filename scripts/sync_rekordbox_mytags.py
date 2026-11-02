#!/usr/bin/env python3
"""Synchronisiert Rekordbox My-Tags für die Schranz-Sammlung (BPM & Version).

Pioneers offizieller Rekordbox-XML-Export unterstützt technisch keine My-Tags.
My-Tags liegen in der SQLite/SQLCipher-Datenbank (master.db) von Rekordbox (Version 6/7).
Dieses Skript liest die Tracks der Schranz-Sammlung (C:\\Users\\user\\Music\\Highres\\Schranz),
ermittelt BPM und Versionstyp (Originale, Edits, Remixe, Bootlegs, etc.) anhand von
ID3-Metadaten (TBPM, TIT2) bzw. Rekordbox-Inhalten und weist vollautomatisch die passenden
My-Tags in master.db zu.

Sicherheitsfeatures:
- Prozess-Check: Prüft vorab, ob rekordbox.exe läuft (bricht ab mit Warnung).
- Automatisches Backup: Erstellt vor jeder schreibenden Änderung ein Backup von master.db.
- Dry-Run-Modus: Simuliert den Ablauf vollständig ohne Datenbankänderungen.
- Idempotenz: Bereits getaggte Tracks werden erkannt; geänderte Werte sauber aktualisiert.

Verwendung:
    # Trockenlauf (zeigt geplante Änderungen, sicher und schreibgeschützt):
    python scripts/sync_rekordbox_mytags.py --dry-run -v

    # Scharf schalten (erfordert beendetes Rekordbox):
    python scripts/sync_rekordbox_mytags.py -v

    # Benutzerdefinierte Pfade:
    python scripts/sync_rekordbox_mytags.py --db-path "C:/path/to/master.db" --collection-dir "C:/path/to/tracks"
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import logging
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Sequence
from uuid import uuid4

import mutagen
import psutil

# Projekt-Root in sys.path aufnehmen, falls direkt aus scripts/ ausgeführt
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from pyrekordbox import MasterDatabase
    import pyrekordbox.masterdb.models as rb_models
except ImportError:
    MasterDatabase = None  # type: ignore
    rb_models = None  # type: ignore

from sc_digger.style import VersionType, detect_version_type

log = logging.getLogger("sync_rekordbox_mytags")

DEFAULT_MASTER_DB = (
    Path.home() / "AppData" / "Roaming" / "Pioneer" / "rekordbox" / "master.db"
)
DEFAULT_COLLECTION_DIR = Path(r"C:\Users\user\Music\Highres\Schranz")

BPM_BUCKETS: list[tuple[str, float, float]] = [
    ("140-145", 140.0, 145.0),
    ("145-150", 145.0, 150.0),
    ("150-155", 150.0, 155.0),
    ("155-160", 155.0, 160.0),
    ("160-165", 160.0, 165.0),
    ("165-170", 165.0, 170.0),
]

VERSION_TAG_NAMES: list[str] = [
    "Originale",
    "Edits",
    "Remixe",
    "Bootlegs",
    "VIPs",
    "Flips",
    "Reworks",
    "Tools",
]

VERSION_TYPE_MAPPING: dict[VersionType, str] = {
    VersionType.ORIGINAL: "Originale",
    VersionType.EDIT: "Edits",
    VersionType.REMIX: "Remixe",
    VersionType.BOOTLEG: "Bootlegs",
    VersionType.VIP: "VIPs",
    VersionType.FLIP: "Flips",
    VersionType.REWORK: "Reworks",
    VersionType.TOOL: "Tools",
    VersionType.MASHUP: "Bootlegs",
}

AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {".wav", ".aif", ".aiff", ".flac", ".mp3", ".m4a"}
)


def is_rekordbox_running() -> bool:
    """Prüft, ob rekordbox.exe aktuell als Prozess aktiv ist."""
    for proc in psutil.process_iter(["name"]):
        try:
            name = proc.info.get("name")
            if name and name.lower() in ("rekordbox.exe", "rekordbox"):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def create_backup(db_path: Path, backup_dir: Path | None = None) -> Path:
    """Erstellt ein zeitgestempeltes Backup der Rekordbox master.db."""
    if not db_path.is_file():
        raise FileNotFoundError(f"Datenbankdatei nicht gefunden: {db_path}")

    target_dir = backup_dir or db_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = target_dir / f"master.db.backup_{timestamp}.bak"

    log.info("Erstelle Backup von master.db: %s", backup_path)
    shutil.copy2(db_path, backup_path)

    if not backup_path.is_file() or backup_path.stat().st_size != db_path.stat().st_size:
        raise IOError(f"Backup-Erstellung fehlgeschlagen oder unvollständig: {backup_path}")

    log.info("Backup erfolgreich erstellt (%d Bytes)", backup_path.stat().st_size)
    return backup_path


def classify_bpm(bpm: float | None) -> str | None:
    """Ordnet eine BPM-Zahl dem entsprechenden Bucket-Tag zu.

    Behebt automatische Oktavfehler (z. B. 79 BPM -> 158 BPM).
    Gibt den Tag-Namen (z.B. '155-160') oder None bei ungültigem Wert zurück.
    """
    if bpm is None or bpm <= 0:
        return None

    # Oktav-Korrektur: Schranz liegt typischerweise bei 140-170 BPM.
    # Rekordbox halbiert Tempi >= 140 gelegentlich auf 70-85 BPM.
    val = float(bpm)
    if val < 100.0:
        val *= 2.0

    for name, low, high in BPM_BUCKETS:
        # Oberste Grenze 170 inklusiv
        if low <= val < high or (high == 170.0 and low <= val <= high):
            return name

    # Außerhalb des Fensters 140-170: nächste Grenze wählen oder None
    if val < 140.0:
        log.warning("BPM %.1f liegt unter dem Schranz-Bereich 140-170", val)
        return "140-145"
    if val > 170.0:
        log.warning("BPM %.1f liegt über dem Schranz-Bereich 140-170", val)
        return "165-170"
    return None


def classify_version(title: str | None, filename: str | None = None) -> str:
    """Ermittelt das Versions-Tag anhand des Titels bzw. des Dateinamens."""
    haystack_title = title.strip() if title else ""
    vt = detect_version_type(haystack_title)

    # Wenn aus dem Titel nur ORIGINAL erkannt wurde, aber der Dateiname ein spezifischeres
    # Muster hat (z. B. '(Schranz Edit)' im Dateinamen), den Dateinamen als Fallback heranziehen.
    if vt == VersionType.ORIGINAL and filename:
        vt_file = detect_version_type(filename)
        if vt_file != VersionType.ORIGINAL:
            vt = vt_file

    return VERSION_TYPE_MAPPING.get(vt, "Originale")


def read_id3_metadata(path: Path) -> tuple[float | None, str | None]:
    """Liest TBPM und TIT2 aus den Audio-Tags via mutagen."""
    if not path.is_file():
        return None, None
    try:
        audio = mutagen.File(path)
        if not audio:
            return None, None

        bpm: float | None = None
        if "TBPM" in audio:
            try:
                raw_bpm = str(audio["TBPM"][0]).strip()
                bpm = float(raw_bpm)
            except (ValueError, IndexError, TypeError):
                bpm = None

        title: str | None = None
        if "TIT2" in audio:
            try:
                title = str(audio["TIT2"][0]).strip()
            except (IndexError, TypeError):
                title = None

        return bpm, title
    except Exception as e:
        log.debug("Fehler beim Lesen der ID3-Tags für %s: %s", path.name, e)
        return None, None


def get_or_create_category(
    db: Any,
    name: str,
    *,
    strategy: str = "auto",
    slot_fallback_names: Sequence[str] = ("Untitled Column", "Situation", "Components"),
    dry_run: bool = False,
) -> Any:
    """Findet oder erstellt eine My-Tag-Kategorie (ParentID='root', Attribute=1).

    In Rekordbox hat das My-Tag-Panel genau 4 Spalten (Slots).
    Mit strategy='auto' oder 'rename-slots' wird bevorzugt eine leere Spalte
    (z. B. 'Untitled Column') umbenannt, damit die Kategorie im Rekordbox-GUI
    sofort sichtbar ist. Mit strategy='create-new' wird eine neue Kategorie angelegt.
    """
    # 1. Existierende Kategorie mit dem Namen suchen (Groß-/Kleinschreibung tolerant)
    categories = (
        db.query(rb_models.DjmdMyTag)
        .filter(rb_models.DjmdMyTag.ParentID == "root")
        .all()
    )
    for cat in categories:
        if cat.Name and cat.Name.strip().lower() == name.lower():
            log.info("Kategorie '%s' gefunden (ID: %s, Seq: %s)", cat.Name, cat.ID, cat.Seq)
            return cat

    # 2. Falls Umbenennung erlaubt/gewünscht ist: Prüfe Fallback-Slots
    if strategy in ("auto", "rename-slots"):
        for fallback in slot_fallback_names:
            for cat in categories:
                if cat.Name and cat.Name.strip().lower() == fallback.lower():
                    # Prüfen, ob dieser Kategorie bereits Tracks zugewiesen sind
                    child_tag_ids = [
                        t.ID
                        for t in db.query(rb_models.DjmdMyTag)
                        .filter(rb_models.DjmdMyTag.ParentID == cat.ID)
                        .all()
                    ]
                    assigned_count = (
                        db.query(rb_models.DjmdSongMyTag)
                        .filter(rb_models.DjmdSongMyTag.MyTagID.in_(child_tag_ids))
                        .count()
                        if child_tag_ids
                        else 0
                    )
                    if assigned_count == 0:
                        log.info(
                            "Benenne Standard-Spalte '%s' (ID: %s) in '%s' um",
                            cat.Name,
                            cat.ID,
                            name,
                        )
                        if not dry_run:
                            cat.Name = name
                            cat.updated_at = datetime.now()
                            db.flush()
                        return cat

    # 3. Neue Root-Kategorie erstellen
    max_seq = max([c.Seq for c in categories if c.Seq is not None] or [0])
    new_id = str(db.generate_unused_id(rb_models.DjmdMyTag))
    log.info("Erstelle neue Kategorie '%s' (neue ID: %s, Seq: %d)", name, new_id, max_seq + 1)
    if dry_run:
        # Dummy-Objekt für Dry-Run
        return rb_models.DjmdMyTag(
            ID=new_id,
            Name=name,
            ParentID="root",
            Attribute=1,
            Seq=max_seq + 1,
        )

    now = datetime.now()
    cat = rb_models.DjmdMyTag.create(
        ID=new_id,
        Seq=max_seq + 1,
        Name=name,
        Attribute=1,
        ParentID="root",
        UUID=str(uuid4()),
        created_at=now,
        updated_at=now,
    )
    db.add(cat)
    db.flush()
    return cat


def get_or_create_tag(
    db: Any,
    category_id: str,
    tag_name: str,
    *,
    dry_run: bool = False,
) -> Any:
    """Findet oder erstellt einen My-Tag innerhalb einer Kategorie."""
    tag = (
        db.query(rb_models.DjmdMyTag)
        .filter(
            rb_models.DjmdMyTag.ParentID == category_id,
            rb_models.DjmdMyTag.Name == tag_name,
        )
        .one_or_none()
    )
    if tag is not None:
        return tag

    existing_tags = (
        db.query(rb_models.DjmdMyTag)
        .filter(rb_models.DjmdMyTag.ParentID == category_id)
        .all()
    )
    max_seq = max([t.Seq for t in existing_tags if t.Seq is not None] or [0])
    new_id = str(db.generate_unused_id(rb_models.DjmdMyTag))
    log.info("Erstelle Tag '%s' in Kategorie ID %s (ID: %s, Seq: %d)", tag_name, category_id, new_id, max_seq + 1)

    if dry_run:
        return rb_models.DjmdMyTag(
            ID=new_id,
            Name=tag_name,
            ParentID=category_id,
            Attribute=0,
            Seq=max_seq + 1,
        )

    now = datetime.now()
    tag = rb_models.DjmdMyTag.create(
        ID=new_id,
        Seq=max_seq + 1,
        Name=tag_name,
        Attribute=0,
        ParentID=category_id,
        UUID=str(uuid4()),
        created_at=now,
        updated_at=now,
    )
    db.add(tag)
    db.flush()
    return tag


def sync_mytags(
    master_db_path: Path = DEFAULT_MASTER_DB,
    collection_dir: Path = DEFAULT_COLLECTION_DIR,
    *,
    dry_run: bool = False,
    force: bool = False,
    category_strategy: str = "auto",
    backup_dir: Path | None = None,
) -> dict[str, Any]:
    """Führt die Synchronisierung der My-Tags für BPM und Version durch.

    Returns:
        Ein Wörterbuch mit Statistiken über den Synchronisierungslauf.
    """
    if MasterDatabase is None or rb_models is None:
        raise RuntimeError("pyrekordbox ist nicht installiert. Bitte installieren mit: pip install pyrekordbox")

    # 1. Sicherheitscheck: Läuft Rekordbox?
    running = is_rekordbox_running()
    if running:
        if not dry_run and not force:
            log.error("Sicherheitscheck fehlgeschlagen: Rekordbox ist aktiv!")
            print("\n" + "=" * 60)
            print("FEHLER: Bitte Rekordbox vorher beenden!")
            print("master.db darf bei geöffnetem Rekordbox nicht verändert werden.")
            print("=" * 60 + "\n")
            return {"status": "aborted", "reason": "rekordbox_running"}
        else:
            log.warning("Rekordbox läuft, aber Dry-Run/Force-Modus ist aktiv.")

    if not master_db_path.is_file():
        raise FileNotFoundError(f"Rekordbox-Datenbank nicht gefunden: {master_db_path}")
    if not collection_dir.is_dir():
        raise FileNotFoundError(f"Sammlungsordner nicht gefunden: {collection_dir}")

    # 2. Backup anlegen (nur bei schreibendem Lauf)
    backup_path = None
    if not dry_run:
        backup_path = create_backup(master_db_path, backup_dir=backup_dir)

    # 3. Datenbank öffnen
    log.info("Öffne master.db: %s (Dry-Run: %s)", master_db_path, dry_run)
    db = MasterDatabase(path=str(master_db_path))

    stats: dict[str, Any] = {
        "status": "success",
        "dry_run": dry_run,
        "backup_path": str(backup_path) if backup_path else None,
        "total_collection_files": 0,
        "matched_rekordbox_tracks": 0,
        "bpm_tags_assigned": Counter(),
        "version_tags_assigned": Counter(),
        "new_song_tags_count": 0,
        "updated_song_tags_count": 0,
        "skipped_unchanged_count": 0,
    }

    try:
        # 4. Kategorien ermitteln oder anlegen
        # Standard: Spalte 4 (Untitled Column) für BPM, Spalte 3 (Situation) für Version
        cat_bpm = get_or_create_category(
            db,
            "BPM",
            strategy=category_strategy,
            slot_fallback_names=["Untitled Column", "Components"],
            dry_run=dry_run,
        )
        cat_version = get_or_create_category(
            db,
            "Version",
            strategy=category_strategy,
            slot_fallback_names=["Situation", "Components", "Untitled Column"],
            dry_run=dry_run,
        )

        # 5. Tags innerhalb der Kategorien ermitteln oder anlegen
        bpm_tags: dict[str, Any] = {}
        for name, _, _ in BPM_BUCKETS:
            bpm_tags[name] = get_or_create_tag(db, cat_bpm.ID, name, dry_run=dry_run)

        version_tags: dict[str, Any] = {}
        for name in VERSION_TAG_NAMES:
            version_tags[name] = get_or_create_tag(db, cat_version.ID, name, dry_run=dry_run)

        # Alle Tag-IDs der jeweiligen Kategorien erfassen (zur Erkennung von Vorbelegungen)
        all_bpm_tag_ids = {t.ID for t in bpm_tags.values()}
        all_ver_tag_ids = {t.ID for t in version_tags.values()}

        # 6. Rekordbox-Tracks einlesen und nach normalisiertem Pfad indizieren
        log.info("Lese Tracks aus Rekordbox master.db...")
        content_by_path: dict[str, Any] = {}
        content_by_filename: dict[str, Any] = {}

        for c in db.get_content():
            if c.FolderPath:
                norm_p = os.path.normpath(c.FolderPath).lower()
                content_by_path[norm_p] = c
            if c.FileNameL:
                content_by_filename[c.FileNameL.lower()] = c

        # 7. Dateien aus der Schranz-Sammlung durchgehen
        log.info("Analysiere Schranz-Sammlung unter %s...", collection_dir)
        collection_files = [
            p
            for p in collection_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
        ]
        stats["total_collection_files"] = len(collection_files)
        log.info("Gefundene Audio-Dateien in der Sammlung: %d", len(collection_files))

        now = datetime.now()

        for file_path in collection_files:
            norm_file = os.path.normpath(str(file_path)).lower()
            content = content_by_path.get(norm_file)
            if not content:
                content = content_by_filename.get(file_path.name.lower())

            if not content:
                log.debug("Track in Rekordbox nicht gefunden: %s", file_path.name)
                continue

            stats["matched_rekordbox_tracks"] += 1
            cid = str(content.ID)

            # BPM und Titel aus ID3 oder Rekordbox-Content lesen
            tag_bpm, tag_title = read_id3_metadata(file_path)

            effective_bpm = tag_bpm
            if effective_bpm is None and content.BPM and content.BPM > 0:
                effective_bpm = content.BPM / 100.0

            effective_title = tag_title or content.Title or file_path.stem

            bpm_bucket = classify_bpm(effective_bpm)
            version_label = classify_version(effective_title, file_path.name)

            if bpm_bucket:
                stats["bpm_tags_assigned"][bpm_bucket] += 1
            stats["version_tags_assigned"][version_label] += 1

            if dry_run:
                stats["new_song_tags_count"] += 2
                continue

            # Vorhandene My-Tag-Zuweisungen für diesen Track abfragen
            existing_song_tags = (
                db.query(rb_models.DjmdSongMyTag)
                .filter(rb_models.DjmdSongMyTag.ContentID == cid)
                .all()
            )
            existing_bpm_tag_ids = {
                st.MyTagID for st in existing_song_tags if st.MyTagID in all_bpm_tag_ids
            }
            existing_ver_tag_ids = {
                st.MyTagID for st in existing_song_tags if st.MyTagID in all_ver_tag_ids
            }

            # A) BPM Tag zuweisen
            if bpm_bucket and bpm_bucket in bpm_tags:
                target_bpm_tag_id = bpm_tags[bpm_bucket].ID
                if target_bpm_tag_id in existing_bpm_tag_ids:
                    stats["skipped_unchanged_count"] += 1
                else:
                    # Ggf. abweichendes altes BPM-Tag entfernen
                    for st in existing_song_tags:
                        if st.MyTagID in all_bpm_tag_ids and st.MyTagID != target_bpm_tag_id:
                            db.delete(st)
                            stats["updated_song_tags_count"] += 1

                    # Neues Tag anfügen
                    st_bpm = rb_models.DjmdSongMyTag.create(
                        ID=str(uuid4()),
                        MyTagID=target_bpm_tag_id,
                        ContentID=cid,
                        TrackNo=1,
                        UUID=str(uuid4()),
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(st_bpm)
                    stats["new_song_tags_count"] += 1

            # B) Version Tag zuweisen
            if version_label in version_tags:
                target_ver_tag_id = version_tags[version_label].ID
                if target_ver_tag_id in existing_ver_tag_ids:
                    stats["skipped_unchanged_count"] += 1
                else:
                    # Ggf. abweichendes altes Version-Tag entfernen
                    for st in existing_song_tags:
                        if st.MyTagID in all_ver_tag_ids and st.MyTagID != target_ver_tag_id:
                            db.delete(st)
                            stats["updated_song_tags_count"] += 1

                    # Neues Tag anfügen
                    st_ver = rb_models.DjmdSongMyTag.create(
                        ID=str(uuid4()),
                        MyTagID=target_ver_tag_id,
                        ContentID=cid,
                        TrackNo=1,
                        UUID=str(uuid4()),
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(st_ver)
                    stats["new_song_tags_count"] += 1

        # 8. Änderungen speichern
        if not dry_run:
            log.info("Speichere Änderungen in master.db...")
            db.commit()
            log.info("Änderungen erfolgreich committet.")
        else:
            log.info("Dry-Run abgeschlossen: Keine Änderungen in master.db geschrieben.")

    finally:
        db.close()
        if hasattr(db, "engine") and db.engine:
            db.engine.dispose()

    return stats


def print_report(stats: dict[str, Any]) -> None:
    """Gibt eine übersichtliche Zusammenfassung der My-Tagging-Aktion aus."""
    print("\n" + "=" * 60)
    mode_str = "DRY-RUN (SIMULATION)" if stats.get("dry_run") else "LIVE (GESCHRIEBEN)"
    print(f"Rekordbox My-Tag Synchronisation – Status: {mode_str}")
    print("=" * 60)

    if stats.get("status") == "aborted":
        print(f"Abbruchgrund: {stats.get('reason')}")
        return

    if stats.get("backup_path"):
        print(f"Backup erstellt unter: {stats['backup_path']}")

    print(f"Dateien in Sammlung:        {stats.get('total_collection_files', 0)}")
    print(f"In Rekordbox zugeordnet:    {stats.get('matched_rekordbox_tracks', 0)}")
    print(f"Neue Tag-Zuweisungen:       {stats.get('new_song_tags_count', 0)}")
    print(f"Aktualisierte Tags:         {stats.get('updated_song_tags_count', 0)}")
    print(f"Unverändert übersprungen:   {stats.get('skipped_unchanged_count', 0)}")

    print("\n--- Verteilung BPM-Tags ---")
    bpm_counts = stats.get("bpm_tags_assigned", Counter())
    for name, _, _ in BPM_BUCKETS:
        print(f"  {name:10s} : {bpm_counts.get(name, 0):4d} Tracks")

    print("\n--- Verteilung Version-Tags ---")
    ver_counts = stats.get("version_tags_assigned", Counter())
    for name in VERSION_TAG_NAMES:
        print(f"  {name:10s} : {ver_counts.get(name, 0):4d} Tracks")

    print("=" * 60 + "\n")


def parse_args(args: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Synchronisiert My-Tags (BPM & Version) in Rekordbox für die Schranz-Sammlung."
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_MASTER_DB,
        help=f"Pfad zur Rekordbox master.db (Standard: {DEFAULT_MASTER_DB})",
    )
    parser.add_argument(
        "--collection-dir",
        type=Path,
        default=DEFAULT_COLLECTION_DIR,
        help=f"Pfad zur Schranz-Sammlung (Standard: {DEFAULT_COLLECTION_DIR})",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="Verzeichnis für das master.db Backup (Standard: gleicher Ordner wie master.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simuliert den Ablauf ohne Änderungen an der Datenbank vorzunehmen.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignoriert die Rekordbox-Prozessprüfung im Dry-Run oder Testumgebungen.",
    )
    parser.add_argument(
        "--category-strategy",
        choices=["auto", "rename-slots", "create-new"],
        default="auto",
        help="Strategie für Kategorien: 'auto' (bevorzugt freie Slots wie 'Untitled Column'), 'rename-slots', oder 'create-new'.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Ausführliche Logging-Ausgaben aktivieren.",
    )
    return parser.parse_args(args)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(asctime)s] %(levelname)-7s - %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        stats = sync_mytags(
            master_db_path=args.db_path,
            collection_dir=args.collection_dir,
            dry_run=args.dry_run,
            force=args.force,
            category_strategy=args.category_strategy,
            backup_dir=args.backup_dir,
        )
        print_report(stats)
        if stats.get("status") == "aborted":
            return 1
        return 0
    except Exception as e:
        log.exception("Fehler während der Synchronisation: %s", e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
