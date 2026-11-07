"""Inbox-Cleanup im Web-Dashboard: Dubletten derselben SoundCloud-ID ansehen und verschieben.

Rein lesend: list_inbox_duplicates(inbox).
Verschieben: move_inbox_duplicates(inbox, lock_dir, soundcloud_id=None).
"""
from __future__ import annotations

import logging
from pathlib import Path

from .. import cleanup, runlock
from ..rekordbox import InboxTrack

log = logging.getLogger(__name__)


def _track_dict(track: InboxTrack, inbox_path: Path) -> dict:
    try:
        rel = track.path.resolve().relative_to(inbox_path.resolve()).as_posix()
    except Exception:
        try:
            rel = track.path.relative_to(inbox_path).as_posix()
        except Exception:
            rel = track.path.name
    key_val = track.key.strip() if track.key and track.key.strip() else None
    return {
        "path": rel,
        "bpm": track.bpm,
        "key": key_val,
        "size_mb": round(track.size / (1024 * 1024), 1),
    }


def list_inbox_duplicates(inbox: str | Path) -> dict:
    """Rein lesend. Nutzt cleanup.audit_inbox_duplicates(Path(inbox)).

    Rückgabe:
      {"ok": True, "total_groups": int, "extra_files": int, "freeable_mb": float,
       "groups": [{"soundcloud_id": str, "title": str,
                   "keep": {"path": str, "bpm": float | None, "key": str | None, "size_mb": float},
                   "duplicates": [{"path": ..., "bpm": ..., "key": ..., "size_mb": ...}, ...]}, ...]}
    - "path" ist der POSIX-Pfad relativ zur Inbox, nie absolut.
    - "key" ist None, wenn der Track keinen Key hat (leerer Text).
    - size_mb auf eine Nachkommastelle gerundet (Bytes / 1024 / 1024); freeable_mb ist die Summe
      der Größen aller überzähligen Dateien, ebenfalls auf eine Stelle gerundet.
    - Reihenfolge der Gruppen wie audit_inbox_duplicates (aufsteigend nach soundcloud_id).
    - inbox fehlt oder existiert nicht: {"ok": True, "total_groups": 0, "extra_files": 0,
      "freeable_mb": 0.0, "groups": []}. Wirft nie; jede Ausnahme: log.exception und derselbe leere Wert.
    """
    empty_result = {
        "ok": True,
        "total_groups": 0,
        "extra_files": 0,
        "freeable_mb": 0.0,
        "groups": [],
    }
    if not inbox:
        return empty_result
    try:
        inbox_path = Path(inbox)
        if not inbox_path.exists():
            return empty_result

        groups = cleanup.audit_inbox_duplicates(inbox_path)
        group_list = []
        total_dup_bytes = 0
        total_dup_files = 0

        for g in groups:
            keep_dict = _track_dict(g.keep, inbox_path)
            dups_list = []
            for d in g.duplicates:
                dups_list.append(_track_dict(d, inbox_path))
                total_dup_bytes += d.size
                total_dup_files += 1

            group_list.append({
                "soundcloud_id": g.soundcloud_id,
                "title": g.keep.title or "",
                "keep": keep_dict,
                "duplicates": dups_list,
            })

        freeable_mb = round(total_dup_bytes / (1024 * 1024), 1)
        return {
            "ok": True,
            "total_groups": len(group_list),
            "extra_files": total_dup_files,
            "freeable_mb": freeable_mb,
            "groups": group_list,
        }
    except Exception:
        log.exception("Fehler beim Auflisten der Inbox-Dubletten")
        return empty_result


def move_inbox_duplicates(
    inbox: str | Path,
    lock_dir: str | Path,
    soundcloud_id: str | None = None,
) -> tuple[int, dict]:
    """Verschiebt überzählige Dateien nach <inbox>/_rejected/duplicate/ und liefert (HTTP-Status, JSON-Body).

    - soundcloud_id nicht None und nicht nur aus Ziffern:
      (400, {"ok": False, "error": "soundcloud_id muss aus Ziffern bestehen"}), nichts passiert.
    - Die Sperre runlock.run_lock(lock_dir, "dashboard-cleanup", wait_s=0.0) wird gehalten, solange
      gescannt und verschoben wird. Ist sie belegt (RunLockBusy):
      (409, {"ok": False, "error": runlock.busy_text(holder)}), nichts passiert.
    - Sonst: groups = cleanup.audit_inbox_duplicates(Path(inbox)); ist soundcloud_id gesetzt, nur
      die Gruppe mit dieser ID (gibt es sie nicht: nichts verschieben). Dann
      cleanup.move_duplicates(groups, Path(inbox)).
      (200, {"ok": True, "moved": <Anzahl verschobener Dateien>, "remaining_groups": <Gruppen, die
      danach noch übrig sind>}). Die behaltene Datei bewegt sich nie.
    - OSError oder andere Ausnahme beim Scannen/Verschieben: log.exception und
      (500, {"ok": False, "error": "Verschieben fehlgeschlagen, siehe Container-Log."}).
      Wirft nie; keine Antwort enthält einen absoluten Pfad.
    """
    if soundcloud_id is not None:
        if not isinstance(soundcloud_id, str) or not soundcloud_id.isdigit():
            return (400, {"ok": False, "error": "soundcloud_id muss aus Ziffern bestehen"})

    try:
        with runlock.run_lock(lock_dir, "dashboard-cleanup", wait_s=0.0):
            try:
                inbox_path = Path(inbox)
                if not inbox_path.exists():
                    return (200, {"ok": True, "moved": 0, "remaining_groups": 0})

                groups = cleanup.audit_inbox_duplicates(inbox_path)
                if soundcloud_id is not None:
                    groups = [g for g in groups if g.soundcloud_id == soundcloud_id]

                moved_files = cleanup.move_duplicates(groups, inbox_path)
                remaining = len(cleanup.audit_inbox_duplicates(inbox_path))
                return (200, {"ok": True, "moved": len(moved_files), "remaining_groups": remaining})
            except Exception:
                log.exception("Fehler beim Verschieben der Inbox-Dubletten")
                return (500, {"ok": False, "error": "Verschieben fehlgeschlagen, siehe Container-Log."})
    except runlock.RunLockBusy as e:
        return (409, {"ok": False, "error": runlock.busy_text(e.holder)})
    except Exception:
        log.exception("Fehler bei Lauf-Sperre oder Verschieben der Inbox-Dubletten")
        return (500, {"ok": False, "error": "Verschieben fehlgeschlagen, siehe Container-Log."})
