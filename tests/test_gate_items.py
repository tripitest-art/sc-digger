"""Tests für Issue #234: Gate-Items in der Track-DB persistieren und im Digest auslesen."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from sc_digger.db import TrackDB
from sc_digger.models import Config, DownloadKind, Track
import sc_digger.output as out
from sc_digger.web.database import query_digest

ROOT = Path(__file__).resolve().parents[1]


def _cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {**cfg["state"], "track_db_path": str(tmp_path / "tracks.sqlite")}
    return cfg


def _track(track_id: int, kind: DownloadKind, link: str | None) -> Track:
    return Track(
        id=track_id, title=f"Track {track_id}", url=f"https://soundcloud.com/a/{track_id}",
        artist="Artist", artist_url="", created_at="2026-10-14T10:00:00Z", duration_ms=300000,
        genre="", tags=[], description="", bpm=None, plays=1000, likes=50,
        reposts=10, comments=2, downloadable=False, has_downloads_left=False,
        purchase_url=None, purchase_title=None,
        download_kind=kind, download_link=link,
    )


def _seen_db(path: Path, rows: list[tuple[int, str, str, str]]) -> None:
    with sqlite3.connect(path) as sdb:
        sdb.execute(
            "CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)"
        )
        sdb.executemany("INSERT INTO seen VALUES (?, ?, ?, ?)", rows)


def test_migration_0010_gate_items_existiert(tmp_path):
    """Migration 0010_gate_items ist eingetragen und die Tabelle existiert."""
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        applied = {
            r[0] for r in db.db.execute("SELECT name FROM schema_migrations").fetchall()
        }
        assert "0010_gate_items" in applied
        tables = {
            r[0] for r in db.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "gate_items" in tables


def test_upsert_gate_item_insert_und_update(tmp_path):
    """upsert_gate_item legt neu an und aktualisiert bei gleicher sc_id last_seen."""
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.upsert_gate_item(1, "Alt", "Artist A", "https://hypeddit.com/alt", "hypeddit")
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        db.db.execute("UPDATE gate_items SET first_seen = ?, last_seen = ? WHERE sc_id = 1", (old_time, old_time))
        db.db.commit()

        db.upsert_gate_item(1, "Neu", "Artist B", "https://droploud.com/neu", "droploud")
        items = db.get_gate_items()
        assert len(items) == 1
        assert items[0]["title"] == "Neu"
        assert items[0]["artist"] == "Artist B"
        assert items[0]["gate_url"] == "https://droploud.com/neu"
        assert items[0]["gate_type"] == "droploud"
        # first_seen bleibt erhalten, last_seen wird auf jetzt gesetzt
        assert items[0]["first_seen"] == old_time
        assert items[0]["last_seen"] != old_time


def test_get_gate_items_sortiert_nach_last_seen(tmp_path):
    """get_gate_items liefert den zuletzt gesehenen Eintrag zuerst."""
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.upsert_gate_item(10, "Alt", "Artist A", "https://hypeddit.com/10")
        db.upsert_gate_item(20, "Neu", "Artist B", "https://toneden.io/20")
        past = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        db.db.execute("UPDATE gate_items SET last_seen = ? WHERE sc_id = 10", (past,))
        db.db.commit()

        ids = [i["sc_id"] for i in db.get_gate_items()]
        assert ids == [20, 10]


def test_build_digest_messages_traegt_gate_track_ein(tmp_path, monkeypatch):
    """build_digest_messages speichert Gate-Tracks per upsert_gate_item in gate_items."""
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(Config, "load", lambda path="config.yaml": cfg)

    blocks = [
        _track(1, DownloadKind.HYPEDDIT, "https://hypeddit.com/1"),
        _track(2, DownloadKind.DROPLOUD, "https://droploud.com/2"),
        _track(3, DownloadKind.NATIVE, "https://soundcloud.com/a/3"),
    ]
    out.build_digest_messages(blocks, max_items=10, header="Test", numbered=True)

    with TrackDB(cfg["state"]["track_db_path"]) as db:
        items = {i["sc_id"]: i for i in db.get_gate_items()}
    assert set(items) == {1, 2}
    assert items[1]["gate_url"] == "https://hypeddit.com/1"
    assert items[1]["gate_type"] == "hypeddit"
    assert items[2]["gate_type"] == "droploud"


def test_build_digest_messages_ignoriert_nicht_angezeigte_gate_tracks(tmp_path, monkeypatch):
    """Wegen max_items nicht angezeigte Gate-Tracks werden nicht eingetragen."""
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(Config, "load", lambda path="config.yaml": cfg)

    t = _track(9, DownloadKind.TONEDEN, "https://toneden.io/9")
    out.build_digest_messages([t], max_items=0, header="Leer", numbered=False)

    with TrackDB(cfg["state"]["track_db_path"]) as db:
        assert db.get_gate_items() == []


def test_query_digest_filter_gate_und_precedence(tmp_path):
    """query_digest setzt download_kind='gate' samt gate_url; Store hat Vorrang."""
    track_db = tmp_path / "tracks.sqlite"
    state_db = tmp_path / "seen.sqlite"

    with TrackDB(track_db) as db:
        db.upsert_gate_item(301, "Hyped", "DJ Gate", "https://hypeddit.com/301", "hypeddit")
        # Store-Track liegt zusätzlich als Gate vor -> Store gewinnt.
        db.upsert_store_item(303, "Kauf", "DJ Store", "https://bandcamp.com/303")
        db.upsert_gate_item(303, "Kauf", "DJ Store", "https://droploud.com/303", "droploud")

    _seen_db(state_db, [
        (301, "https://soundcloud.com/djgate/track", "DJ Gate - Hyped", "2026-10-14 10:00:00"),
        (302, "https://soundcloud.com/djstream/track", "DJ Stream - Normal", "2026-10-14 11:00:00"),
        (303, "https://soundcloud.com/djstore/track", "DJ Store - Kauf", "2026-10-14 12:00:00"),
    ])

    gate = query_digest(state_db, track_db, download_kind="gate")
    assert gate.total == 1
    assert gate.items[0].sc_id == 301
    assert gate.items[0].download_kind == "gate"
    assert gate.items[0].gate_url == "https://hypeddit.com/301"

    all_items = query_digest(state_db, track_db)
    by_id = {i.sc_id: i for i in all_items.items}
    assert by_id[303].download_kind == "store"
    assert by_id[303].gate_url is None
    assert by_id[302].download_kind == "stream"
    assert by_id[302].gate_url is None
