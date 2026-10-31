"""Rückfüll-Werkzeug: Ergänzt fehlende Künstlernamen in seen und rejected aus tracks.sqlite."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import shutil
import sqlite3
import sys

from .linksource import normalize_url
from .output import State


@dataclass(frozen=True)
class BackfillResult:
    seen_updated: int      # Zeilen in seen, die einen Künstler bekommen (haben)
    rejected_updated: int  # dasselbe für rejected
    unmatched: int         # Zeilen in seen+rejected, die danach (bzw. im Probelauf: weiterhin) keinen Künstler haben


def _safe_normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    raw = str(url).strip()
    if not raw:
        return None
    try:
        norm = normalize_url(raw)
        return norm if norm else None
    except Exception:
        return None


def backfill_artists(
    state_db_path: str | Path,
    track_db_path: str | Path,
    *,
    apply: bool = False,
    backup_path: str | Path | None = None,
) -> BackfillResult:
    """Füllt seen.artist und rejected.artist aus tracks.artist.

    Betroffen sind nur Zeilen, deren artist NULL oder leer (nur Leerraum) ist. Ein vorhandener
    Wert wird nie überschrieben. Zuordnung zu tracks: zuerst über tracks.sc_id == Zeilen-id,
    sonst über gleiche Vergleichsform der URL (linksource.normalize_url(tracks.source_url)
    == normalize_url(Zeilen-url), leere URL passt nie). Gesetzt wird tracks.artist, getrimmt;
    ist es leer, zählt die Zeile als unmatched.

    apply=False (Standard, Probelauf): keine Änderung an irgendeiner Datei; die Zahlen
    sagen, was apply=True ändern würde. Fehlt die Spalte artist, gelten alle Zeilen als leer.
    apply=True: backup_path ist Pflicht (None -> ValueError, bevor etwas geändert wird).
    Existiert backup_path schon, FileExistsError, nichts geändert. Sonst wird vor der ersten
    Änderung eine vollständige Kopie der State-Datenbank dorthin geschrieben. Fehlt die Spalte
    artist, wird sie wie in output.State ergänzt (State(path) öffnen und schließen). Alle
    Updates laufen in einer Transaktion.

    Fehlt state_db_path oder track_db_path als Datei: FileNotFoundError. Fehlt die Tabelle
    seen oder rejected bzw. tracks, wird sie wie leer behandelt (kein Fehler).
    Die Track-Datenbank wird nur gelesen.
    """
    state_path = Path(state_db_path)
    track_path = Path(track_db_path)

    if not state_path.is_file():
        raise FileNotFoundError(f"State-Datenbank nicht gefunden: {state_path}")
    if not track_path.is_file():
        raise FileNotFoundError(f"Track-Datenbank nicht gefunden: {track_path}")

    if apply:
        if backup_path is None:
            raise ValueError("backup_path ist Pflicht bei apply=True")
        backup_p = Path(backup_path)
        if backup_p.exists():
            raise FileExistsError(f"Backup-Datei existiert bereits: {backup_p}")

    # 1. Track-DB lesen (nur lesend)
    by_sc_id: dict[int, str] = {}
    by_norm_url: dict[str, str] = {}

    track_uri = f"{track_path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(track_uri, uri=True) as track_db:
        has_tracks_table = bool(
            track_db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tracks'"
            ).fetchone()
        )
        if has_tracks_table:
            cols = {r[1] for r in track_db.execute("PRAGMA table_info(tracks)")}
            sc_id_col = "sc_id" if "sc_id" in cols else "NULL AS sc_id"
            url_col = "source_url" if "source_url" in cols else "NULL AS source_url"
            artist_col = "artist" if "artist" in cols else "NULL AS artist"
            rows = track_db.execute(f"SELECT {sc_id_col}, {url_col}, {artist_col} FROM tracks").fetchall()
            for sc_id, source_url, artist in rows:
                clean_artist = (artist or "").strip() if artist is not None else ""
                if sc_id is not None:
                    try:
                        sc_id_int = int(sc_id)
                        if sc_id_int not in by_sc_id or (not by_sc_id[sc_id_int] and clean_artist):
                            by_sc_id[sc_id_int] = clean_artist
                    except (ValueError, TypeError):
                        pass
                norm_u = _safe_normalize_url(source_url)
                if norm_u:
                    if norm_u not in by_norm_url or (not by_norm_url[norm_u] and clean_artist):
                        by_norm_url[norm_u] = clean_artist

    # 2. State-DB vorbereiten bei apply=True
    if apply:
        shutil.copy2(state_path, backup_p)
        # Fehlende Spalten wie in output.State ergänzen
        with State(state_path):
            pass

    seen_updates: list[tuple[int, str]] = []
    rejected_updates: list[tuple[int, str]] = []
    unmatched = 0

    if apply:
        state_conn = sqlite3.connect(state_path)
    else:
        state_uri = f"{state_path.resolve().as_uri()}?mode=ro"
        state_conn = sqlite3.connect(state_uri, uri=True)

    try:
        for table, updates_list in (("seen", seen_updates), ("rejected", rejected_updates)):
            has_table = bool(
                state_conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
            )
            if not has_table:
                continue

            cols = {r[1] for r in state_conn.execute(f"PRAGMA table_info({table})")}
            artist_select = "artist" if "artist" in cols else "NULL AS artist"
            rows = state_conn.execute(f"SELECT id, url, {artist_select} FROM {table}").fetchall()

            for row_id, row_url, row_artist in rows:
                existing_artist = (row_artist or "").strip() if row_artist is not None else ""
                if existing_artist:
                    # Bereits befüllt, wird nie überschrieben und zählt nicht als unmatched
                    continue

                found = False
                matched_artist = ""

                # Zuordnung zuerst über tracks.sc_id == Zeilen-id
                if row_id in by_sc_id:
                    matched_artist = by_sc_id[row_id]
                    found = True
                else:
                    norm_row_url = _safe_normalize_url(row_url)
                    if norm_row_url and norm_row_url in by_norm_url:
                        matched_artist = by_norm_url[norm_row_url]
                        found = True

                if found and matched_artist:
                    updates_list.append((row_id, matched_artist))
                else:
                    unmatched += 1

        if apply:
            with state_conn:
                for row_id, artist_val in seen_updates:
                    state_conn.execute("UPDATE seen SET artist = ? WHERE id = ?", (artist_val, row_id))
                for row_id, artist_val in rejected_updates:
                    state_conn.execute("UPDATE rejected SET artist = ? WHERE id = ?", (artist_val, row_id))
    finally:
        state_conn.close()

    return BackfillResult(
        seen_updated=len(seen_updates),
        rejected_updated=len(rejected_updates),
        unmatched=unmatched,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m sc_digger.backfill_artist --state-db P --track-db P [--apply --backup P]
    Gibt als erste Ausgabezeile genau
    `seen_updated=<n> rejected_updated=<m> unmatched=<k>` aus. Ohne --apply folgt eine zweite
    Zeile `Probelauf: nichts geschrieben`. --apply ohne --backup beendet mit argparse-Fehler
    (SystemExit mit Code 2), ohne etwas zu ändern. Rückgabe 0 bei Erfolg."""
    parser = argparse.ArgumentParser(
        description="Rückfüllen von seen.artist und rejected.artist aus tracks.artist."
    )
    parser.add_argument("--state-db", required=True, type=Path, help="Pfad zur State-Datenbank (seen.sqlite)")
    parser.add_argument("--track-db", required=True, type=Path, help="Pfad zur Track-Datenbank (tracks.sqlite)")
    parser.add_argument("--apply", action="store_true", help="Änderungen tatsächlich anwenden (erfordert --backup)")
    parser.add_argument("--backup", type=Path, default=None, help="Pfad für Sicherungskopie der State-Datenbank")

    args = parser.parse_args(argv)
    if args.apply and not args.backup:
        parser.error("--apply erfordert --backup")

    res = backfill_artists(
        args.state_db,
        args.track_db,
        apply=args.apply,
        backup_path=args.backup,
    )

    print(f"seen_updated={res.seen_updated} rejected_updated={res.rejected_updated} unmatched={res.unmatched}")
    if not args.apply:
        print("Probelauf: nichts geschrieben")
    return 0


if __name__ == "__main__":
    sys.exit(main())
